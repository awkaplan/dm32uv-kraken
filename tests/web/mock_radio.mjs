// A simulated DM-32UV on the end of a fake Web Serial port, for tests/web_flasher_test.mjs.
// At a normal power-on it is the HR_C7000 boot ROM (hdboot_serial.ksy in dm32uv-re): it takes the reader
// in DOWNLOAD frames and RUNs it, then plays our reader (reader/reader.c) speaking the Kraken reader
// protocol (web/lib/kr.js), baud changes included. With SK1+PTT it is Baofeng's updater. Faults can be
// injected: corrupted or lost reader frames, a wrong baud rate, the cable going dead mid-flash.

import { crc32, frame, rd16, rd32, T, CHUNK } from "../../web/lib/kr.js";

const APP = 0xc000;
const FLASH = 0x100000;

function crc16(data) {
  let crc = 0;
  for (const b of data) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) crc = (crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1) & 0xffff;
  }
  return crc;
}
const sum8 = (a) => a.reduce((s, b) => (s + b) & 0xff, 0);
const rd32be = (a, o) => ((a[o] << 24) | (a[o + 1] << 16) | (a[o + 2] << 8) | a[o + 3]) >>> 0;
const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

export class MockRadio {
  constructor(flash, opts = {}) {
    this.flash = new Uint8Array(flash);
    // uartClock: what UART0 runs from in boot-ROM mode; romDivisor: what the ROM programs for 115200.
    // maxHostBaud: the cable chip's limit (the host's open() fails above it, as a real driver may).
    this.opts = { bootWindowMs: 3000, eraseMs: 40, uartClock: 24000000, romDivisor: 13, maxHostBaud: 2000000, ...opts };
    this.uploaded = [];
    this.corrupted = new Set();
    this.state = "off";
    this.inbuf = [];
    this.events = [];
    this.readReplies = 0;
    this.flipped = new Set();
    this.blocksWritten = 0;
    this.controller = null;
    const radio = this;
    this.port = {
      readable: null,
      writable: null,
      getInfo() {
        return radio.opts.usbInfo || {};
      },
      async open(opts) {
        if (radio.usb.claimed) throw new Error("mock: the port is in use");
        if (opts.baudRate > radio.opts.maxHostBaud) throw new Error(`mock: ${opts.baudRate} baud not supported`);
        // appleProlific: macOS's PL2303 driver runs the line at 9600 for any rate a browser sets above 38400
        radio.hostBaud = radio.opts.appleProlific && opts.baudRate > 38400 ? 9600 : opts.baudRate;
        radio.serialOpen = true;
        radio.port.readable = new ReadableStream({ start: (c) => (radio.controller = c) });
        radio.port.writable = new WritableStream({ write: (chunk) => radio.receive(chunk) });
      },
      async close() {
        try {
          radio.controller.close();
        } catch {}
        radio.port.readable = null;
        radio.serialOpen = false;
      },
    };
    // The same cable as a WebUSB device: a PL2303 (start-up vendor requests, SET/GET_LINE_CODING, two bulk
    // pipes). The interface can be claimed only while the serial port is closed (opts.usbClaimFails: never).
    const usb = (this.usb = {
      vendorId: 0x067b,
      productId: 0x2303,
      deviceClass: 0,
      opened: false,
      claimed: false,
      rates: [],
      coding: new Uint8Array(7),
      queue: [],
      waiters: [],
      configuration: {
        configurationValue: 1,
        interfaces: [{
          interfaceNumber: 0,
          alternate: {
            endpoints: [
              { direction: "in", type: "interrupt", endpointNumber: 1, packetSize: 10 },
              { direction: "out", type: "bulk", endpointNumber: 2, packetSize: 64 },
              { direction: "in", type: "bulk", endpointNumber: 3, packetSize: 64 },
            ],
          },
        }],
      },
      async open() {
        usb.opened = true;
      },
      async close() {
        usb.opened = usb.claimed = false;
        for (const w of usb.waiters.splice(0)) w.reject(new Error("mock: device closed"));
        usb.queue = [];
      },
      async claimInterface() {
        if (radio.opts.usbClaimFails || radio.serialOpen) throw new Error("mock: Unable to claim interface.");
        usb.claimed = true;
      },
      async clearHalt() {},
      async controlTransferOut(setup, data) {
        if (setup.requestType === "class") {
          if (setup.recipient !== "interface" || !usb.claimed) return { status: "stall" };
          if (setup.request === 0x20) {
            usb.coding = Uint8Array.from(data);
            const code = (data[0] | (data[1] << 8) | (data[2] << 16) | (data[3] << 24)) >>> 0;
            const baud = code & 0x80000000 ? 384e6 / ((code & 0x1ff) * 4 ** ((code >> 9) & 7)) : code;
            radio.hostBaud = baud;
            usb.rates.push(Math.round(baud));
          }
        }
        return { status: "ok", bytesWritten: data ? data.length : 0 };
      },
      async controlTransferIn(setup, n) {
        const bytes = setup.requestType === "class" && setup.request === 0x21 ? usb.coding : new Uint8Array(n);
        return { status: "ok", data: new DataView(Uint8Array.from(bytes).buffer) };
      },
      async transferOut(ep, data) {
        if (!usb.claimed) throw new Error("mock: interface not claimed");
        radio.receive(Uint8Array.from(data));
        return { status: "ok", bytesWritten: data.length };
      },
      transferIn(ep, size) {
        if (!usb.claimed) return Promise.reject(new Error("mock: interface not claimed"));
        return new Promise((resolve, reject) => {
          usb.waiters.push({ resolve, reject, size });
          usb.feed();
        });
      },
      // hand queued bytes to waiting transfers, a packet each
      feed() {
        while (usb.queue.length && usb.waiters.length) {
          const w = usb.waiters.shift();
          const part = Uint8Array.from(usb.queue.splice(0, w.size));
          w.resolve({ status: "ok", data: new DataView(part.buffer) });
        }
      },
    });
  }

  // Bytes from the radio. If the two ends disagree on the baud rate the host sees noise.
  emit(bytes) {
    if (this.state === "dead") return;
    let out = Uint8Array.from(bytes);
    if (!this.ratesMatch()) out = out.map((b, i) => (b * 7 + i) & 0x5f);
    if (this.usb.claimed) {
      this.usb.queue.push(...out);
      this.usb.feed();
      return;
    }
    try {
      this.controller.enqueue(out);
    } catch {}
  }

  radioBaud() {
    return this.state.startsWith("kr") ? this.opts.uartClock / (16 * this.divisor) : 115200;
  }

  ratesMatch() {
    if (this.opts.garbleAbove && this.hostBaud > this.opts.garbleAbove) return false;
    return Math.abs(this.hostBaud - this.radioBaud()) / this.radioBaud() < 0.03;
  }

  powerOff() {
    clearInterval(this.hs);
    clearTimeout(this.win);
    this.state = "off";
    this.inbuf = [];
    this.events.push("off");
  }

  powerOn(mode = "normal") {
    this.powerOff();
    this.events.push(`on:${mode}`);
    if (mode === "updater") {
      this.state = "upd:greet";
      this.expectBlock = undefined;
      return;
    }
    this.state = "rom:wait";
    this.uploaded = [];
    this.hs = setInterval(() => this.emit([0x02, 0x24, 0x00, 0x03]), 100);
    this.emit([0x02, 0x24, 0x00, 0x03]);
    this.win = setTimeout(() => {
      if (this.state === "rom:wait") {
        clearInterval(this.hs);
        this.state = "app"; // nobody answered: the stock firmware boots and ignores the cable
      }
    }, this.opts.bootWindowMs);
  }

  receive(chunk) {
    if (this.state === "off" || this.state === "app" || this.state === "dead") return;
    if (!this.ratesMatch()) return; // garbled at the radio's end
    this.inbuf.push(...chunk);
    for (;;) {
      const used = this.step();
      if (!used) break;
      this.inbuf.splice(0, used);
    }
  }

  // Handle one complete message at the front of inbuf; return how many bytes it used (0: need more)
  step() {
    const b = this.inbuf;
    const s = this.state;
    if (s === "rom:wait") {
      const i = b.findIndex((_, k) => eq(b.slice(k, k + 4), [0x02, 0x14, 0x00, 0x03]));
      if (i < 0) return b.length > 3 ? b.length - 3 : 0;
      clearInterval(this.hs);
      clearTimeout(this.win);
      this.state = "rom:load";
      return i + 4;
    }
    if (s === "rom:load") {
      if (b.length < 4) return 0;
      if (b[0] !== 0x02 || b[3] !== 0x03) throw new Error(`mock ROM: bad frame header ${b.slice(0, 4)}`);
      const cmd = b[1];
      if (cmd === 0x18) { // DOWNLOAD addr, word count, words
        if (b.length < 12) return 0;
        const count = rd32be(b, 8);
        if (count > 0x40) throw new Error("mock ROM: DOWNLOAD over 64 words");
        if (b.length < 12 + count * 4) return 0;
        const body = b.slice(4, 12 + count * 4);
        if (sum8(body) !== b[2]) throw new Error("mock ROM: DOWNLOAD checksum");
        this.uploaded.push({ addr: rd32be(b, 4), data: Uint8Array.from(b.slice(12, 12 + count * 4)) });
        this.emit([0x02, 0x05, 0x00, 0x03]);
        return 12 + count * 4;
      }
      if (cmd === 0x1b) { // RUN addr
        if (b.length < 8) return 0;
        if (sum8(b.slice(4, 8)) !== b[2]) throw new Error("mock ROM: RUN checksum");
        this.runAt = rd32be(b, 4);
        this.emit([0x02, 0x05, 0x00, 0x03]);
        if (this.opts.onRun) this.opts.onRun(this);
        this.startReader();
        return 8;
      }
      throw new Error(`mock ROM: unexpected command ${cmd.toString(16)}`);
    }
    if (s === "kr" || s === "kr:baud") return this.kr(b);
    if (s.startsWith("upd:")) return this.updater(b);
    return b.length;
  }

  // --- our reader ---

  startReader() {
    this.state = "kr";
    this.divisor = this.opts.romDivisor;
    this.dataFrames = 0;
    // HELLO: "KRD", version 1, the divisor UART0 runs at
    this.emitFrame(T.HELLO, [0x4b, 0x52, 0x44, 1, this.divisor & 0xff, this.divisor >> 8, 0, 0]);
  }

  emitFrame(type, payload) {
    this.emit(frame(type, Uint8Array.from(payload)));
  }

  kr(b) {
    if (b.length < 2) return 0;
    if (b[0] !== 0x5a || b[1] !== 0xa5) return 1;
    if (b.length < 5) return 0;
    const len = b[3] | (b[4] << 8);
    if (b.length < 5 + len + 4) return 0;
    const body = Uint8Array.from(b.slice(2, 5 + len));
    if (crc32(body) !== rd32(Uint8Array.from(b.slice(5 + len, 9 + len)), 0)) return 5 + len + 4; // dropped, as the reader does
    const type = body[0], p = body.subarray(3);
    this.streamId = (this.streamId || 0) + 1; // a new command stops a READ in progress
    if (this.state === "kr:baud") {
      if (type !== T.PING) return 5 + len + 4;
      clearTimeout(this.baudTimer);
      this.state = "kr";
    }
    if (type === T.PING) this.emitFrame(T.PONG, p);
    else if (type === T.BAUD) {
      const div = rd16(p, 0), ms = rd16(p, 2);
      this.emitFrame(T.OK, []);
      const old = this.divisor;
      this.divisor = div;
      this.state = "kr:baud";
      this.baudTimer = setTimeout(() => {
        if (this.state === "kr:baud") {
          this.divisor = old;
          this.state = "kr";
        }
      }, ms);
    } else if (type === T.READ) this.stream(rd32(p, 0), rd32(p, 4));
    else if (type === T.SUMS) {
      const addr = rd32(p, 0), n = rd32(p, 4), step = rd32(p, 8);
      const out = [];
      for (let a = addr; a < addr + n; a += step) {
        const v = crc32(this.flash.subarray(a, Math.min(a + step, addr + n)));
        out.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, v >>> 24);
      }
      this.emitFrame(T.SUMSR, out);
    } else this.emitFrame(T.ERR, [1]);
    return 5 + len + 4;
  }

  async stream(addr, n) {
    const id = this.streamId;
    for (let a = addr; a < addr + n && this.streamId === id && this.state === "kr"; a += CHUNK) {
      const k = ++this.dataFrames;
      this.totalDataFrames = (this.totalDataFrames || 0) + 1;
      const data = Array.from(this.flash.subarray(a, Math.min(a + CHUNK, addr + n)));
      if (this.opts.misreadOnce && this.opts.misreadOnce >= a && this.opts.misreadOnce < a + data.length) {
        data[this.opts.misreadOnce - a] ^= 0x04; // a wrong byte inside a good frame: only the sector CRC catches it
        this.opts.misreadOnce = 0;
      }
      let f = frame(T.DATA, Uint8Array.from([a & 0xff, (a >>> 8) & 0xff, (a >>> 16) & 0xff, a >>> 24, ...data]));
      if (this.opts.corruptFrames && this.opts.corruptFrames.includes(k) && !this.corrupted.has(k)) {
        this.corrupted.add(k);
        f = Uint8Array.from(f);
        f[200] ^= 0x01;
      }
      if (this.opts.loseFrames && this.opts.loseFrames.includes(k) && !this.corrupted.has(k)) {
        this.corrupted.add(k);
        f = f.subarray(0, 1000); // cut short: the host times out on it
      }
      this.emit(f);
      await new Promise((r) => setTimeout(r, 0));
    }
  }

  // Baofeng's updater as dm32uv-re reads it (web/lib/radio.js has the summary): top loop 'R' / '1',
  // the 5-byte command handler, and the YMODEM receiver that answers 'C' to every block's first byte.
  updater(b) {
    if (!b.length) return 0;
    const s = this.state;
    const reply = (bytes, next) => {
      this.state = next;
      this.emit(bytes);
    };
    if (s === "upd:greet") {
      if (b[0] === 0x52) reply([0x06], "upd:cmd");
      else if (b[0] === 0x31) reply([0x43], "upd:hdr");
      return 1;
    }
    if (s === "upd:cmd") {
      if (b.length < 5) return 0;
      if (b[0] === 0x4d) reply([0x4d, 0x01, 0x09], "upd:model");
      else if (b[0] === 0x45) reply([0x06], "upd:greet");
      else throw new Error(`mock updater: command ${b[0].toString(16)}`);
      return 5;
    }
    if (s === "upd:model") {
      if (b[0] !== 0x06) throw new Error(`mock updater: expected 06, got ${b.slice(0, 8)}`);
      reply([..."BFUV32-V2"].map((c) => c.charCodeAt(0)), "upd:cmd");
      return 1;
    }
    if (s === "upd:hdr") {
      const h = b[0];
      if (h === 0x04) {
        this.events.push("reboot");
        this.emit([0x43]);
        this.state = "off";
        return 1;
      }
      if (h !== 0x01 && h !== 0x02) throw new Error(`mock updater: header ${h.toString(16)}`);
      this.hdr = h;
      reply([0x43], "upd:body");
      return 1;
    }
    if (s === "upd:body") {
      const n = this.hdr === 0x01 ? 128 : 1024;
      if (b.length < n + 4) return 0;
      if (b[0] !== (0xff ^ b[1])) throw new Error("mock updater: block number check");
      const data = Uint8Array.from(b.slice(2, 2 + n));
      if (this.hdr === 0x01 && b[0] === 0) { // block 0: name and size (an STX block 256 also reads 0: data)
        const z = data.indexOf(0);
        const size = parseInt(String.fromCharCode(...data.subarray(z + 1, z + 17)).split(" ")[0], 10);
        this.declared = size;
        if (!(size > 0) || size > 0xf4002) {
          this.emit([0x18, 0x18]);
          this.state = "off";
          return n + 4;
        }
        this.flash.fill(0xff, APP);
        this.events.push("erase");
        this.expectBlock = 1;
        this.blockAt = APP;
        this.state = "upd:erasing";
        setTimeout(() => {
          this.state = "upd:hdr";
          this.emit([0x06, 0x43]);
        }, this.opts.eraseMs);
        return n + 4;
      }
      if (this.expectBlock === undefined) throw new Error("mock updater: data before block 0");
      if (b[0] !== (this.expectBlock & 0xff)) throw new Error(`mock updater: block ${b[0]}, expected ${this.expectBlock & 0xff}`);
      if (crc16(data) !== ((b[2 + n] << 8) | b[3 + n])) {
        this.emit([0x18, 0x18]);
        this.state = "off";
        return n + 4;
      }
      if (this.blockAt + n > FLASH) throw new Error("mock updater: write past the end of flash");
      this.flash.set(data, this.blockAt);
      this.blockAt += n;
      this.expectBlock++;
      this.blocksWritten++;
      if (this.opts.dieAfterBlocks && this.blocksWritten === this.opts.dieAfterBlocks) {
        this.opts.dieAfterBlocks = 0;
        this.state = "dead"; // cable pulled: no answer, until the next power-on
        this.events.push("dead");
        return n + 4;
      }
      reply([0x06], "upd:hdr");
      return n + 4;
    }
    return b.length;
  }
}

// What the updater leaves after writing FILE (web/lib/radio.js pads the last block with 0xFF)
export function simulateUpdate(flash, file) {
  const out = new Uint8Array(flash);
  out.fill(0xff, APP);
  out.set(file.subarray(0x100), APP);
  return out;
}
