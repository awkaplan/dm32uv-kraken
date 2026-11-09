// The Kraken reader protocol: how the page talks to reader/reader.c once the boot ROM has started it.
// Written for this installer (docs/web-flasher.md, section 3); both ends are in this repository.
//
// Every message, both ways:  5A A5 <type> <len u16 LE> <payload> <CRC-32 LE of type, len and payload>
//
//   host -> reader                         reader -> host
//   01 PING  <any bytes>                   81 PONG  <the same bytes>
//   02 BAUD  <divisor u16> <ms u16>        82 OK    (sent at the old rate; then the reader switches and
//                                                     waits ms for a PING at the new rate, else goes back)
//   03 READ  <addr u32> <len u32>          83 DATA  <addr u32> <up to 4096 bytes>, one per 4 KB, in order
//   04 SUMS  <addr u32> <len u32> <step u32>  84 SUMS <CRC-32 u32 of each step-sized piece>, up to 1024
//   (any error)                            8F ERR   <code u8>
//   HELLO, unasked, when the reader starts: 80 HELLO "KRD" <version u8> <divisor u16> <reserved u16>
//
// Addresses are offsets into program flash (0 = 0x03000000). The reader only ever reads flash.

import { concat, u8 } from "./bytes.js";
import { TimeoutError } from "./link.js";

export const SYNC = [0x5a, 0xa5];
export const T = { PING: 0x01, BAUD: 0x02, READ: 0x03, SUMS: 0x04, HELLO: 0x80, PONG: 0x81, OK: 0x82, DATA: 0x83, SUMSR: 0x84, ERR: 0x8f };
export const CHUNK = 0x1000;
export const MAX_SUMS = 1024;

const TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(data, crc = 0) {
  crc = ~crc >>> 0;
  for (let i = 0; i < data.length; i++) crc = TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  return ~crc >>> 0;
}

const le16 = (n) => u8([n & 0xff, (n >>> 8) & 0xff]);
const le32 = (n) => u8([n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff]);
export const rd16 = (a, o) => a[o] | (a[o + 1] << 8);
export const rd32 = (a, o) => (a[o] | (a[o + 1] << 8) | (a[o + 2] << 16) | (a[o + 3] << 24)) >>> 0;

export function frame(type, payload = new Uint8Array(0)) {
  const body = concat(u8([type]), le16(payload.length), payload);
  return concat(u8(SYNC), body, le32(crc32(body)));
}

export class FrameError extends Error {}

// The next whole frame: { type, payload }. Skips noise before the sync bytes. FrameError on a bad CRC.
// Each frame goes to the link's trace as its header only (lib/diag.js keeps no DATA payload).
export async function readFrame(link, ms) {
  const deadline = Date.now() + ms;
  const left = () => Math.max(1, deadline - Date.now());
  let prev = -1;
  let skipped = 0;
  for (;;) {
    const b = (await link.read(1, left()))[0];
    if (prev === SYNC[0] && b === SYNC[1]) break;
    prev = b;
    skipped++;
  }
  const noise = Math.max(0, skipped - 1);
  const head = await link.read(3, left());
  const len = rd16(head, 1);
  if (len > CHUNK + 16) {
    link.trace?.frame({ type: head[0], len, ok: false, noise, why: "too long" });
    throw new FrameError(`frame too long (${len})`);
  }
  const rest = await link.read(len + 4, left());
  const body = concat(head, rest.subarray(0, len));
  if (crc32(body) !== rd32(rest, len)) {
    link.trace?.frame({ type: head[0], len, ok: false, noise, why: "CRC mismatch" });
    throw new FrameError("frame CRC mismatch");
  }
  const payload = rest.subarray(0, len);
  link.trace?.frame({ type: head[0], len, payload, noise });
  return { type: head[0], payload };
}

export class Reader {
  constructor(link, { log = () => {} } = {}) {
    this.link = link;
    this.log = log;
    this.divisor = 0;
  }

  async hello(ms = 3000) {
    const f = await readFrame(this.link, ms);
    if (f.type !== T.HELLO || f.payload[0] !== 0x4b || f.payload[1] !== 0x52 || f.payload[2] !== 0x44) throw new FrameError("no HELLO from the reader");
    this.version = f.payload[3];
    this.divisor = rd16(f.payload, 4);
    return this;
  }

  async request(type, payload, want, ms = 2000) {
    await this.link.write(frame(type, payload));
    const f = await readFrame(this.link, ms);
    if (f.type === T.ERR) throw new FrameError(`reader error ${f.payload[0]}`);
    if (f.type !== want) throw new FrameError(`unexpected reply ${f.type.toString(16)}`);
    return f.payload;
  }

  async ping(tag = 0x4b52) {
    const p = await this.request(T.PING, le16(tag), T.PONG, 800);
    return rd16(p, 0) === tag;
  }

  // After an error: the reader stops streaming when a new command arrives. PING with a fresh tag and drop
  // everything until its PONG, so no stale frame of the old request is taken for new data.
  async resync() {
    for (let i = 0; i < 4; i++) {
      const tag = (Math.random() * 0xffff) | 0;
      await this.link.discard(50);
      await this.link.write(frame(T.PING, le16(tag)));
      const deadline = Date.now() + 2500;
      try {
        while (Date.now() < deadline) {
          const f = await readFrame(this.link, deadline - Date.now());
          if (f.type === T.PONG && rd16(f.payload, 0) === tag) return;
        }
      } catch (e) {
        if (!(e instanceof FrameError || e instanceof TimeoutError)) throw e;
      }
    }
    throw new FrameError("the reader stopped answering");
  }

  // CRC-32 of each STEP-sized piece of [addr, addr+len), computed on the radio
  async sums(addr, len, step) {
    const out = [];
    for (let a = addr; a < addr + len; ) {
      const n = Math.min(len - (a - addr), step * MAX_SUMS);
      const p = await this.request(T.SUMS, concat(le32(a), le32(n), le32(step)), T.SUMSR, 10000);
      for (let i = 0; i < p.length; i += 4) out.push(rd32(p, i));
      a += n;
    }
    return out;
  }

  // Flash [addr, addr+len) into out[addr...]. One request streams up to 64 KB as 4 KB DATA frames; a bad
  // or missing frame ends that request and it is asked for again from the first missing byte.
  async read(out, addr, len, onProgress = () => {}) {
    const end = addr + len;
    let at = addr, tries = 0;
    while (at < end) {
      const n = Math.min(end - at, 0x10000);
      try {
        await this.link.write(frame(T.READ, concat(le32(at), le32(n))));
        const stop = at + n;
        while (at < stop) {
          const f = await readFrame(this.link, 3000);
          if (f.type === T.ERR) throw new FrameError(`reader error ${f.payload[0]}`);
          if (f.type !== T.DATA || rd32(f.payload, 0) !== at) throw new FrameError("out-of-order data");
          const data = f.payload.subarray(4);
          out.set(data, at);
          at += data.length;
          tries = 0;
          onProgress((at - addr) / len);
        }
      } catch (e) {
        if (!(e instanceof FrameError || e instanceof TimeoutError)) throw e;
        if (++tries > 6) throw e;
        this.log(`read at ${at.toString(16)}: ${e.message}; asking again`);
        await this.resync();
      }
    }
  }
}
