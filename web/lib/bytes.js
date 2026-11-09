// Small byte helpers shared by the installer modules. No DOM, so Node tests can import them.

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function u8(arr) {
  return Uint8Array.from(arr);
}

export function ascii(str) {
  return Uint8Array.from(str, (c) => c.charCodeAt(0));
}

export function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

export function equal(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

export function allBytes(a, value) {
  for (let i = 0; i < a.length; i++) if (a[i] !== value) return false;
  return true;
}

export function be32(n) {
  return u8([(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff]);
}

export function sum8(a) {
  let s = 0;
  for (const b of a) s = (s + b) & 0xff;
  return s;
}

export function hex(a) {
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function addr(n) {
  return "0x" + (0x03000000 + n).toString(16).padStart(8, "0");
}

export function fromBase64(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function sha256(data) {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", data)));
}

// CRC-16/XMODEM, as the radio's updater checks it on every block
export function crc16(data) {
  let crc = 0;
  for (const b of data) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) crc = (crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1) & 0xffff;
  }
  return crc;
}

// Printable text at the start of a byte run (stops at the first non-printable byte)
export function text(a) {
  let s = "";
  for (const b of a) {
    if (b < 0x20 || b > 0x7e) break;
    s += String.fromCharCode(b);
  }
  return s;
}
