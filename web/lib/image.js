// The radio's program-flash image: checks, the Kraken patch, the update file and the read-back check.
// Mirrors tools/make_official_update.py (build and verify) and tools/make_web_patch.py. No DOM.

import { allBytes, ascii, concat, equal, fromBase64, sha256, text, addr } from "./bytes.js";

export const FLASH_SIZE = 0x100000;
export const APP = 0xc000; // the updater erases and writes from 0x0300C000 up
export const UNIT = 0xb000; // per-unit sector 0x0300B000 (binding record, TX-band model byte)
export const SECTOR = 0x1000;
export const BLOCK = 0x400; // updater block size
export const SIZE_LIMIT = 0xf4002; // the updater refuses a larger file (header included)
export const VERSION_AT = 0xadaa0; // where DM32.00.01.048 keeps its version string
// Baofeng's public download, DM32.01.L01.048, also ends in 048 but is a different build line: its string
// is at 0xab858, and 0xadaa0 holds code pointers there. Only used to name the build in a refusal.
export const L01 = "DM32.01.L01.048";
export const VERSION_SPOTS = [VERSION_AT, 0xab858];
const VERSION_RE = /^DM32\.[0-9A-Z]{2}\.[0-9A-Z]{2,4}\.[0-9]{3}$/;
export const BOOT_MAGIC = ascii("thd\x0c");
export const PATCH_FORMAT = "kraken-web-patch-1";

export const HEADER = (() => {
  const h = new Uint8Array(0x100).fill(0xff);
  h.set(ascii("BFUV32-V2"), 0);
  h.set(ascii("END"), 0xfd);
  return h;
})();

export class CheckError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.code = code;
    this.detail = detail;
  }
}

// --- the patch file (kraken_patch.json, written by tools/make_web_patch.py) ---

export function loadPatch(json) {
  const p = json;
  if (!p || p.format !== PATCH_FORMAT) throw new CheckError("patch", `The Kraken patch file has an unknown format (${p && p.format}).`);
  for (const k of ["stock", "target", "update", "records", "firmware", "reader"]) {
    if (!(k in p)) throw new CheckError("patch", `The Kraken patch file has no "${k}".`);
  }
  const records = p.records.map(([at, b64]) => ({ at, data: fromBase64(b64) }));
  for (const r of records) {
    if (r.at < APP || r.at + r.data.length > FLASH_SIZE) throw new CheckError("patch", `The Kraken patch file writes outside the firmware area (${addr(r.at)}).`);
  }
  const s = p.stock, t = p.target;
  if (!(s.end > APP && s.end <= FLASH_SIZE && t.end > APP && t.end <= FLASH_SIZE)) throw new CheckError("patch", "The Kraken patch file has bad bounds.");
  if (p.update.size !== HEADER.length + (t.end - APP) || p.update.size > SIZE_LIMIT) throw new CheckError("patch", "The Kraken patch file's update size does not add up.");
  const n = (FLASH_SIZE - APP) / SECTOR;
  if (s.sector_crc32?.length !== n || t.sector_crc32?.length !== n) throw new CheckError("patch", "The Kraken patch file has no sector CRCs.");
  return { ...p, records };
}

// --- what the radio holds ---

// The firmware's version string and where it is: { version, at }, or { version: "", at: -1 }. Looks at the
// known spots first, then anywhere in IMG (a whole image, or one with only some sectors read in).
export function findVersion(img) {
  const at24 = (a) => text(img.subarray(a, a + 24));
  for (const a of VERSION_SPOTS) if (VERSION_RE.test(at24(a))) return { version: at24(a), at: a };
  for (let a = img.indexOf(0x44); a >= 0; a = img.indexOf(0x44, a + 1)) {
    if (img[a + 1] === 0x4d && img[a + 2] === 0x33 && img[a + 3] === 0x32 && img[a + 4] === 0x2e && VERSION_RE.test(at24(a))) {
      return { version: at24(a), at: a };
    }
  }
  return { version: "", at: -1 };
}

export function versionOf(img) {
  return findVersion(img).version;
}

// Is IMG on the build this patch is for? Only its own spot counts, wherever else a string turns up.
export function isFirmware(img, patch) {
  return text(img.subarray(VERSION_AT, VERSION_AT + 24)).startsWith(patch.firmware);
}

// What to tell the person when the version is wrong. WHAT is "This radio" or "That backup".
export function wrongVersionMessage(v, patch, what) {
  if (v === L01) {
    return `${what} has ${L01}, Baofeng’s public download. It also ends in 048, but it is a different firmware line. Kraken works only on ${patch.firmware}.`;
  }
  return `${what} has firmware ${v || "of an unknown version"}. Kraken needs ${patch.firmware}.`;
}

// Checks that need only the first block and the version sectors (read into a 1 MB buffer, before the
// full read)
export function quickChecks(head, patch) {
  const out = [];
  out.push({
    id: "boot",
    label: "Program memory has the DM-32 boot header",
    ok: equal(head.subarray(0, 4), BOOT_MAGIC),
  });
  const v = versionOf(head);
  out.push({
    id: "version",
    label: `Firmware is ${patch.firmware}`,
    ok: isFirmware(head, patch),
    detail: v ? `The radio reports ${v}.` : "The radio reports no version string.",
  });
  return out;
}

// After the stock firmware area: one partial 1 KB block of padding from an earlier update, then erased.
function tailOk(img, end) {
  const padEnd = Math.min(FLASH_SIZE, APP + Math.ceil((end - APP) / BLOCK) * BLOCK);
  const pad = img.subarray(end, padEnd);
  if (pad.length && !allBytes(pad, pad[0])) return false;
  return allBytes(img.subarray(padEnd), 0xff);
}

// 'stock' | 'kraken' (this exact build) | 'other'
export async function classify(img, patch) {
  if ((await sha256(img.subarray(APP, patch.stock.end))) === patch.stock.sha256 && tailOk(img, patch.stock.end)) return "stock";
  if ((await sha256(img.subarray(APP, patch.target.end))) === patch.target.sha256 && tailOk(img, patch.target.end)) return "kraken";
  return "other";
}

// --- building what the updater gets ---

// The Kraken image from a stock one: the stock firmware area with Kraken's records on top
export async function applyPatch(img, patch) {
  const out = new Uint8Array(img);
  out.fill(0xff, patch.stock.end);
  for (const r of patch.records) out.set(r.data, r.at);
  if ((await sha256(out.subarray(APP, patch.target.end))) !== patch.target.sha256 || !allBytes(out.subarray(patch.target.end), 0xff)) {
    throw new CheckError("patch-result", "The patched firmware does not match this Kraken build. Nothing was written to the radio.");
  }
  return out;
}

export function updateFile(body) {
  const file = concat(HEADER, body);
  if (file.length > SIZE_LIMIT) throw new CheckError("size", `The update file is ${file.length} bytes, over the updater's limit.`);
  return file;
}

export async function krakenUpdate(stockImg, patch) {
  const img = await applyPatch(stockImg, patch);
  const file = updateFile(img.subarray(APP, patch.target.end));
  if ((await sha256(file)) !== patch.update.sha256) throw new CheckError("patch-result", "The Kraken update file does not match this build. Nothing was written to the radio.");
  return file;
}

// A stock image's own firmware area as an update file (for going back to stock)
export function stockUpdate(stockImg, patch) {
  return updateFile(stockImg.subarray(APP, patch.stock.end));
}

// --- after flashing ---

// What program flash holds after the updater wrote FILE (radio.js pads its last 1 KB block with 0xFF,
// the erased value, so this is the image itself).
// LOW: the flash below 0x0300C000 if known (the updater never writes it); else zeros there.
export function expectedFlash(file, low) {
  const out = new Uint8Array(FLASH_SIZE).fill(0xff);
  if (low) out.set(low.subarray(0, APP));
  else out.fill(0, 0, APP);
  const body = file.subarray(HEADER.length);
  out.set(body, APP);
  return out;
}
