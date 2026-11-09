// The cable protocols the installer speaks.
//
//  - Reading: at power-on the HR_C7000's mask boot ROM offers NOTIFY (02 24 00 03) on UART0. We answer
//    DEBUG (02 14 00 03), DOWNLOAD our reader (reader/reader.c, built with Kraken) into IRAM and RUN it.
//    The ROM protocol is Dahua's (HR_C7000 datasheet; dm32uv-re specs/ksy/hdboot_serial.ksy). From then
//    on the page speaks the Kraken reader protocol (kr.js). The reader only reads flash.
//  - Flashing: the radio's own updater (SK1 + PTT at power-on, green LED). It erases 0x0300C000 up and
//    writes the file body there in 1 KB blocks with a CRC16 each. It cannot write below 0x0300C000, so the
//    boot loader and the per-unit sector are never touched, and a failed flash can always be redone.
//
// There is deliberately no code here that writes program flash any other way.

import { be32, concat, crc16, equal, sleep, sum8, u8, ascii } from "./bytes.js";
import { Link, TimeoutError, CancelledError } from "./link.js";
import { Reader, FrameError } from "./kr.js";

export class RadioError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const NOTIFY = u8([0x02, 0x24, 0x00, 0x03]);
const DEBUG = u8([0x02, 0x14, 0x00, 0x03]);
const OK = u8([0x02, 0x05, 0x00, 0x03]);
const ROM_DOWNLOAD = 0x18;
const ROM_RUN = 0x1b;
const BASE_BAUD = 115200;

async function expect(link, want, ms, what) {
  let got;
  try {
    got = await link.read(want.length, ms);
  } catch (e) {
    if (e instanceof TimeoutError) throw new RadioError("no-reply", `The radio did not answer (${what}).`);
    throw e;
  }
  if (!equal(got, want)) throw new RadioError("bad-reply", `Unexpected answer from the radio (${what}).`);
}

function romFrame(cmd, body) {
  return concat(u8([0x02, cmd, sum8(body), 0x03]), body);
}

// Wait for the boot ROM as the radio powers on, load our reader and start it. Returns a kr.js Reader.
// reader: { bin, load, entry } from the site's kraken_patch.json.
export async function startReader(link, reader, { waitMs = 60000 } = {}) {
  await link.discard();
  try {
    await link.waitFor(NOTIFY, waitMs);
  } catch (e) {
    if (e instanceof TimeoutError) {
      throw new RadioError("no-boot", "The radio didn\u2019t answer. Check the cable is pushed fully into the radio, then try again.");
    }
    throw e;
  }
  await link.write(DEBUG);
  await link.discard(50);
  const bin = reader.bin;
  for (let at = 0; at < bin.length; at += 0x100) {
    const words = bin.subarray(at, at + 0x100);
    const body = concat(be32(reader.load + at), be32(words.length / 4), words);
    await link.write(romFrame(ROM_DOWNLOAD, body));
    await expect(link, OK, 1500, "loading the reader");
  }
  await link.write(romFrame(ROM_RUN, be32(reader.entry)));
  await expect(link, OK, 1500, "starting the reader");
  const r = new Reader(link, { log: link.log });
  try {
    await r.hello(3000);
  } catch (e) {
    if (e instanceof FrameError || e instanceof TimeoutError) throw new RadioError("no-reader", "The radio stopped answering. Turn it off and try again.");
    throw e;
  }
  return r;
}

// The fastest rate worth trying on this cable, from its USB IDs (port.getInfo()). The reader's fast
// rates are 24 MHz / 16 / n (1.5M, 750k, 500k). CH340 and FTDI chips make those exactly. Prolific PL2303
// cables (067B), the common Baofeng one, stay at 115200: on the one measured (driven directly, with the
// chip's divisor codes) 1.5M did nothing and 750k to 300k only worked from the radio to the computer.
// Other chips are tried; the reader falls back by itself.
export function cableMaxBaud(info = {}) {
  return info.usbVendorId === 0x067b ? BASE_BAUD : 2000000;
}

// Faster than 115200 if the cable and the radio's UART clock allow it. The rates tried are the exact
// ones the UART can make (its clock / 16 / divisor) between 230400 and 2 Mbaud. The reader goes back to
// the old rate by itself if no PING arrives at the new one, so a failed try costs about a second.
export async function speedUp(env, reader, { maxBaud = 2000000 } = {}) {
  // In boot-ROM mode UART0 runs from the 24 MHz oscillator (divisor 13 for 115200). Use the exact clock
  // when the divisor says so, so the rates asked of the cable are exact (1.5M, 750k, 500k).
  const est = BASE_BAUD * 16 * reader.divisor;
  const clock = Math.abs(est - 24e6) / 24e6 < 0.01 ? 24e6 : est;
  const tries = [];
  for (let div = 1; div < reader.divisor; div++) {
    const baud = Math.round(clock / 16 / div);
    if (baud <= maxBaud && baud >= 230400) tries.push({ div, baud });
  }
  for (const { div, baud } of tries.slice(0, 3)) {
    try {
      await reader.request(0x02, concat(u8([div & 0xff, div >> 8]), u8([0xdc, 0x05])), 0x82, 1500); // BAUD div, 1500 ms
    } catch (e) {
      if (e instanceof FrameError || e instanceof TimeoutError) break;
      throw e;
    }
    let ok = false;
    try {
      reader.link = await env.reopen(baud);
      ok = await reader.ping();
    } catch (e) {
      if (e instanceof CancelledError) throw e;
      env.log(`${baud} baud: ${e.message}`);
    }
    if (ok) {
      reader.baud = baud;
      return baud;
    }
    env.log(`${baud} baud did not work; back to ${BASE_BAUD}`);
    reader.link = await env.reopen(BASE_BAUD);
    await sleep(1600); // the reader goes back after 1.5 s
    await reader.resync();
  }
  reader.baud = BASE_BAUD;
  return BASE_BAUD;
}

// --- the updater ---
//
// Baofeng's updater, in the bootloader at 0x03001000 (entered with SK1 + PTT), as reverse engineered in
// dm32uv-re: its top loop (0x0300455c) reads one byte. 'R' runs the command handler (0x030016ac): it
// sends 06, then takes 5-byte commands ('M' -> 'M' 01 <len>, then after our 06 the model string
// "BFUV32-V2"; 'E' -> 06 and back to the top loop). '1' starts its YMODEM receiver (0x03004618), which
// sends 'C' and then, for every block, sends 'C' again as soon as it has the block's first byte:
//   block 0  SOH 00 FF <128 bytes: name NUL size-in-decimal SPACE> <CRC16>: checks the size against its
//            limit, erases 0x0300C000 up (~15 s), then 06 'C'
//   data     STX <n> <~n> <1024 bytes> <CRC16 BE>, n = 1, 2, ... mod 256: CRC-checked, programmed, 06
//   EOT      04: 'C', then it finishes and restarts the radio
// A bad CRC or a size over the limit gets CAN CAN (18 18).

const SOH = 0x01, STX = 0x02, EOT = 0x04, ACK = 0x06, CAN = 0x18, C = 0x43;
const BLOCK = 0x400;

async function expectByte(link, want, ms, what) {
  let b;
  try {
    b = (await link.read(1, ms))[0];
  } catch (e) {
    if (e instanceof TimeoutError) throw new RadioError("no-reply", `The radio stopped answering (${what}).`);
    throw e;
  }
  if (b === CAN) throw new RadioError("refused", `The radio refused the update (${what}).`);
  if (b !== want) throw new RadioError("bad-reply", `Unexpected answer from the radio (${what}).`);
}

// Send a BFUV32-V2 update FILE (header + firmware area) to the radio's updater; it restarts the radio.
export async function flashUpdate(link, file, { onProgress = () => {}, onPhase = () => {}, eraseMs = 30000, name = "kraken.bin" } = {}) {
  await link.discard(100);
  // 'R': the updater answers 06. Retry a little: the person may click just before it is up.
  let greeted = false;
  for (let i = 0; i < 3 && !greeted; i++) {
    await link.write(u8([0x52]));
    try {
      greeted = (await link.read(1, 1200))[0] === ACK;
    } catch (e) {
      if (!(e instanceof TimeoutError)) throw e;
    }
    if (!greeted) await link.discard(200);
  }
  if (!greeted) {
    throw new RadioError("no-updater", "The radio's updater did not answer. Turn the radio off, hold SK1 and PTT, turn it on, and let go when the LED is green. Then try again.");
  }
  await link.write(ascii("M\0\0\0\0"));
  await expect(link, u8([0x4d, 0x01, 0x09]), 1500, "updater info");
  await link.write(u8([ACK]));
  await expect(link, ascii("BFUV32-V2"), 1500, "radio model");
  await link.write(ascii("E\0\0\0\0"));
  await expectByte(link, ACK, 1500, "updater mode");
  await link.write(ascii("1"));
  await expectByte(link, C, 1500, "starting the transfer");

  // Block 0: from here the firmware area is being replaced. A failure is recoverable by flashing again.
  onPhase("erase");
  const info = new Uint8Array(128);
  info.set(concat(ascii(name), u8([0]), ascii(`${file.length} `)));
  const c0 = crc16(info);
  await link.write(u8([SOH]));
  await expectByte(link, C, 1500, "file header");
  await link.write(concat(u8([0x00, 0xff]), info, u8([c0 >> 8, c0 & 0xff])));
  await expectByte(link, ACK, eraseMs, "erasing");
  await expectByte(link, C, 1500, "erasing");

  onPhase("write");
  const body = file.subarray(0x100);
  const blocks = Math.ceil(body.length / BLOCK);
  for (let n = 1; n <= blocks; n++) {
    const block = new Uint8Array(BLOCK).fill(0xff); // the last block is padded with the erased value
    block.set(body.subarray((n - 1) * BLOCK, n * BLOCK));
    const k = crc16(block);
    await link.write(u8([STX]));
    await expectByte(link, C, 2000, `block ${n} of ${blocks}`);
    await link.write(concat(u8([n & 0xff, 0xff - (n & 0xff)]), block, u8([k >> 8, k & 0xff])));
    await expectByte(link, ACK, 3000, `block ${n} of ${blocks}`);
    onProgress(n / blocks);
  }
  onPhase("restart");
  await link.write(u8([EOT]));
  try {
    await expectByte(link, C, 2000, "finishing");
  } catch (e) {
    // every block was ACKed, so the firmware is written; the check after the restart says for sure
    if (!(e instanceof RadioError)) throw e;
    link.log(`no answer to EOT (${e.message}); every block was acknowledged`);
  }
  await sleep(300);
}
