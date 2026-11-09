// A byte link over a Web Serial port (or anything shaped like one: tests use a simulated radio).
// Reads are exact-length with a deadline, so a dropped byte becomes a timeout and a retry, never a shifted
// stream. The port stays open across the radio's power cycles: the USB-serial chip is in the cable.
// trace (optional): a lib/diag.js Recorder that sees every byte both ways, for the diagnostics download.

import { sleep } from "./bytes.js";

export class TimeoutError extends Error {}
export class CancelledError extends Error {
  constructor() {
    super("Cancelled.");
  }
}

export class Link {
  constructor(port, { log = () => {}, trace = null } = {}) {
    this.port = port;
    this.log = log;
    this.trace = trace;
    this.chunks = [];
    this.length = 0;
    this.wake = null;
    this.cancelled = false;
    this.closing = false;
    this.lost = null;
  }

  async open(baud = 115200) {
    await this.port.open({ baudRate: baud, dataBits: 8, stopBits: 1, parity: "none", flowControl: "none", bufferSize: 1 << 16 });
    this.baud = baud;
    this.trace?.event(`port open at ${baud} baud`);
    this.writer = this.port.writable.getWriter();
    this.loop = this.readLoop();
  }

  async readLoop() {
    // A break or framing error (likely while the radio powers off or on) ends one reader; take a new one.
    while (this.port.readable && !this.closing) {
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          if (value && value.length) {
            this.trace?.rx(value, this.baud);
            this.chunks.push(value);
            this.length += value.length;
            this.notify();
          }
        }
      } catch (e) {
        if (!this.closing) {
          this.log(`serial: ${e.name || "error"} ${e.message || ""} (continuing)`);
          this.trace?.event(`serial read error: ${e.name || "error"} ${e.message || ""}`);
        }
      } finally {
        this.reader.releaseLock();
      }
    }
    if (!this.closing) {
      this.lost = new Error("The cable was disconnected.");
      this.trace?.event("port lost: the cable was disconnected");
    }
    this.notify();
  }

  notify() {
    if (this.wake) this.wake();
  }

  waitData(ms) {
    return new Promise((resolve) => {
      const t = setTimeout(() => {
        this.wake = null;
        resolve();
      }, ms);
      this.wake = () => {
        clearTimeout(t);
        this.wake = null;
        resolve();
      };
    });
  }

  check() {
    if (this.cancelled) throw new CancelledError();
    if (this.lost) throw this.lost;
  }

  cancel() {
    this.cancelled = true;
    this.notify();
  }

  resume() {
    this.cancelled = false;
  }

  take(n) {
    const out = new Uint8Array(n);
    let at = 0;
    while (at < n) {
      const c = this.chunks[0];
      const k = Math.min(c.length, n - at);
      out.set(c.subarray(0, k), at);
      at += k;
      if (k === c.length) this.chunks.shift();
      else this.chunks[0] = c.subarray(k);
    }
    this.length -= n;
    return out;
  }

  // Exactly n bytes, or TimeoutError after ms
  async read(n, ms) {
    const deadline = Date.now() + ms;
    while (this.length < n) {
      this.check();
      const left = deadline - Date.now();
      if (left <= 0) throw new TimeoutError(`got ${this.length} of ${n} bytes`);
      await this.waitData(left);
    }
    this.check();
    return this.take(n);
  }

  // Consume bytes until PATTERN has gone by (the boot handshake), or TimeoutError after ms
  async waitFor(pattern, ms) {
    const deadline = Date.now() + ms;
    let pos = 0;
    for (;;) {
      while (this.length > 0) {
        const b = this.take(1)[0];
        pos = b === pattern[pos] ? pos + 1 : b === pattern[0] ? 1 : 0;
        if (pos === pattern.length) return;
      }
      this.check();
      const left = deadline - Date.now();
      if (left <= 0) throw new TimeoutError("pattern not seen");
      await this.waitData(Math.min(left, 500));
    }
  }

  // Throw away whatever has arrived, after letting stragglers in for ms
  async discard(ms = 0) {
    if (ms) await sleep(ms);
    this.trace?.discarded(this.length);
    this.chunks = [];
    this.length = 0;
  }

  // what: a label for bytes the trace must not keep (firmware blocks); they are traced as length and CRC
  async write(bytes, what) {
    this.check();
    this.trace?.tx(bytes, this.baud, what);
    await this.writer.write(bytes);
  }

  async close() {
    this.closing = true;
    this.trace?.event(`port close (was ${this.baud} baud)`);
    try {
      if (this.reader) await this.reader.cancel();
    } catch {}
    try {
      await this.loop;
    } catch {}
    try {
      this.writer.releaseLock();
    } catch {}
    try {
      await this.port.close();
    } catch {}
  }
}
