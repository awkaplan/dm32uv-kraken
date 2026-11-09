#!/usr/bin/env node
// The web installer (web/) end to end against a simulated radio (tests/web/mock_radio.mjs): the real
// flows, link, protocols and patch code, with a scripted person in place of the page.
//
//   node tests/web_flasher_test.mjs [--stock STOCK.bin] [--out out/kraken]
//
// Needs a build (out/kraken/: the update file, web/kraken_patch.json and web/reader.bin) and the stock
// image from dm32uv-re (found as tools/build.sh finds it, or --stock). The simulated radio plays the
// boot ROM and our reader's protocol; the reader's own code is tested in QEMU (tests/reader_qemu_test.py).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MockRadio, simulateUpdate } from "./web/mock_radio.mjs";
import { loadPatch, APP, UNIT, VERSION_AT, SECTOR } from "../web/lib/image.js";
import { install, installFromBackup, restoreStock } from "../web/lib/flow.js";
import { Recorder } from "../web/lib/diag.js";
import { zip } from "../web/lib/zip.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = Object.fromEntries(process.argv.slice(2).reduce((a, x, i, all) => (x.startsWith("--") ? [...a, [x.slice(2), all[i + 1]]] : a), []));

function findStock() {
  if (args.stock) return args.stock;
  const rel = "data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin";
  const roots = [process.env.DM32UV_RE, path.join(REPO, "../dm32uv-re"), path.join(REPO, "../dm32/dm32uv-re")].filter(Boolean);
  for (const r of roots) if (fs.existsSync(path.join(r, rel))) return path.join(r, rel);
  throw new Error("no stock image: pass --stock or set DM32UV_RE");
}

const outDir = path.resolve(REPO, args.out || "out/kraken");
const STOCK = new Uint8Array(fs.readFileSync(findStock()));
const UPDATE = new Uint8Array(fs.readFileSync(path.join(outDir, "kraken_update_BFUV32-V2.bin")));
const PATCH = loadPatch(JSON.parse(fs.readFileSync(path.join(outDir, "web/kraken_patch.json"), "utf8")));
const READER_BIN = new Uint8Array(fs.readFileSync(path.join(outDir, "web/reader.bin")));
const READER = { bin: READER_BIN, load: PATCH.reader.load, entry: PATCH.reader.entry };

let failures = 0;
function ok(cond, what) {
  console.log(`  ${cond ? "PASS" : "FAIL"} ${what}`);
  if (!cond) failures++;
}
const same = (a, b) => a.length === b.length && Buffer.compare(Buffer.from(a), Buffer.from(b)) === 0;

// A radio of its own: stock, with a made-up per-unit sector
function stockRadio() {
  const f = new Uint8Array(STOCK);
  for (let i = 0; i < SECTOR; i++) f[UNIT + i] = (i * 37 + 11) & 0xff;
  return f;
}

// The person: does what each prompt says, unless the test says otherwise
function person(radio, { onAsk = {}, wrongUpdaterMode = false } = {}) {
  const seen = { asks: [], checks: {}, saved: null, done: null, statuses: [], bauds: [] };
  const open = radio.port.open;
  radio.port.open = async (o) => {
    await open(o);
    seen.bauds.push(o.baudRate);
  };
  const ui = {
    phase() {},
    log() {},
    progress() {},
    status(t) {
      seen.statuses.push(t);
    },
    show({ id }) {
      if (id === "on") setTimeout(() => radio.powerOn("normal"), 30);
    },
    check(id, label, okv, detail) {
      seen.checks[id] = { ok: okv, detail };
    },
    async ask({ id, title, button }) {
      if (!id || !title || !button) ok(false, `screen ${id} has an id, a title and a button`);
      seen.asks.push(id);
      if (onAsk[id]) return onAsk[id](radio, seen);
      if (id === "off" || id === "verify") radio.powerOff();
      if (id === "update-mode") {
        radio.powerOff();
        setTimeout(() => radio.powerOn(wrongUpdaterMode ? "normal" : "updater"), 30);
      }
      return "primary";
    },
    async saveBackup(bytes) {
      radio.powerOff();
      seen.saved = new Uint8Array(bytes);
    },
    done(d) {
      seen.done = d;
    },
  };
  return { ui, seen };
}

function envFor(radio, more = {}) {
  return { patch: PATCH, reader: READER, log: () => {}, requestPort: async () => radio.port, ...more };
}

// A Mac: WebUSB is there. `allowed` is whether the page was already given the cable as a USB device.
function macEnv(radio, { allowed = false } = {}) {
  const usb = { prompts: 0 };
  const env = envFor(radio, {
    platform: "mac",
    usbDevice: async (filter, prompt) => {
      if (filter.vendorId !== radio.usb.vendorId || filter.productId !== radio.usb.productId) return null;
      if (allowed) return radio.usb;
      if (!prompt) return null;
      usb.prompts++;
      return radio.usb;
    },
  });
  return { env, usb };
}

async function scenario(name, fn) {
  console.log(`\n${name}`);
  const t = Date.now();
  try {
    await fn();
  } catch (e) {
    ok(false, `threw ${e.code || e.name}: ${e.message}`);
  }
  console.log(`  (${((Date.now() - t) / 1000).toFixed(1)} s)`);
}

async function expectError(p, code) {
  try {
    await p;
  } catch (e) {
    return e.code === code ? e : (ok(false, `error code ${e.code} (${e.message}), expected ${code}`), e);
  }
  ok(false, `no error, expected ${code}`);
  return null;
}

// --- the patch alone ---
await scenario("patch: stock + records = this build's update file", async () => {
  const { krakenUpdate, classify } = await import("../web/lib/image.js");
  ok(same(await krakenUpdate(stockRadio(), PATCH), UPDATE), "update file is byte-identical to out/kraken/kraken_update_BFUV32-V2.bin");
  ok((await classify(stockRadio(), PATCH)) === "stock", "a stock image classifies as stock");
  ok((await classify(simulateUpdate(stockRadio(), UPDATE), PATCH)) === "kraken", "an installed image classifies as kraken");
  const carried = PATCH.records.reduce((n, r) => n + r.data.filter((b, k) => b === STOCK[r.at + k] && b !== 0xff).length, 0);
  ok(carried === 0, `no unchanged stock byte is carried in the patch (${carried})`);
});

// --- the one-button flow ---
await scenario("install: stock radio; our reader loaded and run; fastest rate; CRC-checked backup", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before);
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  const up = radio.uploaded;
  const sent = new Uint8Array(up.reduce((n, f) => n + f.data.length, 0));
  up.forEach((f) => sent.set(f.data, f.addr - PATCH.reader.load));
  ok(same(sent, READER_BIN) && up[0].addr === PATCH.reader.load, `the ROM got exactly reader.bin at ${PATCH.reader.load.toString(16)} (${up.length} DOWNLOAD frames)`);
  ok(radio.runAt === PATCH.reader.entry, "RUN at the reader's entry");
  ok(radio.totalDataFrames >= 258, `backup streamed in 4 KB frames (${radio.totalDataFrames} in all)`);
  ok(seen.saved && same(seen.saved, before), "saved backup is exactly the radio's flash");
  ok(same(radio.flash, simulateUpdate(before, UPDATE)), "radio holds the Kraken update, boot area and per-unit sector untouched");
  ok(seen.checks.version?.ok === true && seen.checks.stock?.ok === true, "version and stock checks passed");
  ok(seen.checks.verify?.ok === true, "post-install CRC check passed");
  ok(seen.done?.id === "installed", "finished");
  ok(seen.bauds.includes(1500000), `ran the reader at 1.5 Mbaud (${[...new Set(seen.bauds)].join(", ")})`);
});

await scenario("install: corrupted and cut-off frames are asked for again", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { corruptFrames: [3, 100], loseFrames: [200] });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(radio.corrupted.size === 3, "three frames were damaged");
  ok(seen.saved && same(seen.saved, before), "saved backup is exact");
});

await scenario("install: a wrong byte in a good frame is caught by the sector CRC", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { misreadOnce: 0x5123 });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(radio.opts.misreadOnce === 0, "the byte was misread once");
  ok(seen.saved && same(seen.saved, before), "saved backup is exact");
});

await scenario("install: cable chip limited to 1 Mbaud: the next rate down", async () => {
  const radio = new MockRadio(stockRadio(), { maxHostBaud: 1000000 });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.bauds.includes(750000) && !seen.bauds.includes(1500000), `ran at 750 kbaud (${[...new Set(seen.bauds)].join(", ")})`);
  ok(seen.done?.id === "installed", "finished");
});

await scenario("install: Prolific PL2303 cable: stays at 115200 without trying fast rates", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, garbleAbove: 115200 });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.bauds.every((b) => b === 115200), `only 115200 opened (${[...new Set(seen.bauds)].join(", ")})`);
  ok(seen.saved && same(seen.saved, before), "saved backup is exact");
  ok(seen.done?.id === "installed", "finished");
});

await scenario("install on a Mac with a Prolific cable: Apple's driver is bypassed over WebUSB", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, appleProlific: true });
  const { ui, seen } = person(radio);
  const { env, usb } = macEnv(radio);
  await install(env, ui);
  ok(seen.asks.includes("usb-access") && usb.prompts === 1, "asked once for the cable as a USB device");
  ok(seen.bauds.length === 0, "the serial port was never opened");
  ok(radio.usb.rates.length > 0 && radio.usb.rates.every((b) => b === 115200), `the chip was set to 115200 directly (${[...new Set(radio.usb.rates)].join(", ")})`);
  ok(seen.saved && same(seen.saved, before), "saved backup is exact");
  ok(seen.done?.id === "installed" && same(radio.flash, simulateUpdate(before, UPDATE)), "installed and the flash is exactly the build");
});

await scenario("Mac, Prolific, USB access already allowed: no extra prompt", async () => {
  const radio = new MockRadio(stockRadio(), { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, appleProlific: true });
  const { ui, seen } = person(radio);
  const { env, usb } = macEnv(radio, { allowed: true });
  await install(env, ui);
  ok(!seen.asks.includes("usb-access") && usb.prompts === 0, "no prompt");
  ok(seen.done?.id === "installed", "finished");
});

await scenario("Mac, Prolific, another driver holds the cable: falls back to its serial port", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, usbClaimFails: true });
  const { ui, seen } = person(radio);
  const { env } = macEnv(radio);
  await install(env, ui);
  ok(radio.usb.rates.length === 0 && seen.bauds.includes(115200), "used the serial port");
  ok(seen.saved && same(seen.saved, before) && seen.done?.id === "installed", "finished");
});

await scenario("Mac, Prolific, Apple's driver and no USB access: stops with the radio never heard", async () => {
  const radio = new MockRadio(stockRadio(), { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, appleProlific: true, usbClaimFails: true });
  const { ui, seen } = person(radio);
  const { env } = macEnv(radio);
  env.bootWaitMs = 400;
  const e = await expectError(install(env, ui), "no-boot");
  ok(/allow access/.test(e?.message || ""), "the message says to allow access to the cable");
  ok(!radio.events.includes("erase"), "nothing written");
});

await scenario("install: fast rates open but garble: falls back to 115200", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { garbleAbove: 115200 });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.saved && same(seen.saved, before), "saved backup is exact at 115200");
  ok(seen.done?.id === "installed", "finished");
});

await scenario("install: wrong firmware line (DM32.01.L01.048) stops before the backup", async () => {
  const before = stockRadio();
  before.set(Buffer.from("DM32.01.L01.048"), VERSION_AT);
  const radio = new MockRadio(before);
  const { ui, seen } = person(radio);
  await expectError(install(envFor(radio), ui), "version");
  ok(seen.checks.version?.ok === false && /L01/.test(seen.checks.version.detail), "version check failed and names what the radio reports");
  ok(radio.dataFrames <= 2, `only two sectors were read (${radio.dataFrames})`);
  ok(seen.saved === null && !radio.events.includes("erase"), "no backup prompt, nothing written");
});

await scenario("install: a radio already on this Kraken build (known from CRCs, no backup read)", async () => {
  const radio = new MockRadio(simulateUpdate(stockRadio(), UPDATE));
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.done?.id === "already", "says it is already installed");
  ok(radio.dataFrames <= 2 && !radio.events.includes("erase"), "nothing read beyond the checks, nothing written");
});

await scenario("install: modified firmware (an earlier Kraken build) is refused from CRCs", async () => {
  const f = stockRadio();
  for (let s = 0x20000; s < 0x40000; s += SECTOR) f[s + 5] ^= 0xff;
  const radio = new MockRadio(f);
  const { ui, seen } = person(radio);
  await expectError(install(envFor(radio), ui), "not-stock");
  ok(seen.checks.stock?.ok === false, "stock check failed");
  ok(radio.dataFrames <= 2 && seen.saved === null && !radio.events.includes("erase"), "no backup read, nothing written");
});

await scenario("install: stock restored earlier with padding in its last block is still stock", async () => {
  const f = stockRadio();
  const pad = APP + Math.ceil((PATCH.stock.end - APP) / 1024) * 1024;
  f.fill(0x00, PATCH.stock.end, pad);
  const radio = new MockRadio(f);
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.saved && same(seen.saved, f), "backed up and installed");
  ok(seen.done?.id === "installed", "finished");
});

await scenario("install: the radio is not in update mode, then the person stops", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { bootWindowMs: 300 });
  const { ui, seen } = person(radio, { wrongUpdaterMode: true, onAsk: { "no-updater": () => "secondary" } });
  await expectError(install(envFor(radio), ui), "stopped");
  ok(seen.asks.includes("no-updater"), "told the person the updater did not answer");
  ok(same(radio.flash, before), "nothing written");
});

await scenario("install: the cable dies mid-flash, the person tries again", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { dieAfterBlocks: 300 });
  const { ui, seen } = person(radio);
  await install(envFor(radio), ui);
  ok(seen.asks.includes("flash-failed"), "told the person the install did not finish");
  ok(radio.events.filter((e) => e === "erase").length === 2, "flashed twice");
  ok(same(radio.flash, simulateUpdate(before, UPDATE)), "radio holds the Kraken update after the retry");
  ok(seen.checks.verify?.ok === true, "post-install CRC check passed");
});

await scenario("install: a bad write is caught by the post-install CRC check", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before);
  let damaged = false;
  const { ui, seen } = person(radio, {
    onAsk: {
      "verify": (r) => {
        if (!damaged) {
          damaged = true;
          r.flash[0x60000] ^= 0x80;
        }
        r.powerOff();
        return "primary";
      },
    },
  });
  await install(envFor(radio), ui);
  ok(seen.asks.includes("verify-failed"), "noticed the bad sector and offered to install again");
  ok(same(radio.flash, simulateUpdate(before, UPDATE)), "radio holds the Kraken update after the second install");
});

// --- the diagnostics recorder (web/lib/diag.js) on the real flows ---

// Trace events whose kept bytes contain 12 bytes in a row of the stock firmware or the update file
function firmwareInTrace(diag) {
  const hay = [Buffer.from(STOCK), Buffer.from(UPDATE)];
  const found = [];
  for (const e of diag.trace.items()) {
    if (!e.hex || e.hex.length < 24) continue;
    const b = Buffer.from(e.hex, "hex");
    for (let i = 0; i + 12 <= b.length; i++) {
      const w = b.subarray(i, i + 12);
      if (new Set(w).size < 6) continue; // padding (a space and zeros, say) is in any image
      if (hay.some((h) => h.indexOf(w) >= 0)) {
        found.push(e);
        break;
      }
    }
  }
  return found;
}

async function diagFiles(diag) {
  const files = diag.files({ page: { release: "test" } });
  return { files, by: Object.fromEntries(files.map((f) => [f.name, f.data])), size: (await zip(files)).length };
}

await scenario("diagnostics: a full install leaves facts and a trace, and no firmware", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { corruptFrames: [50] });
  const { ui, seen } = person(radio);
  const diag = new Recorder();
  await install(envFor(radio, { diag }), ui);
  ok(seen.done?.id === "installed", "finished");
  const f = diag.facts;
  ok(f.reader?.baud === 1500000 && f.reader.tries[0]?.result === "ok", `reader facts (${JSON.stringify(f.reader)})`);
  ok(f.radio?.version.startsWith(PATCH.firmware) && f.radio.kind === "stock" && f.radio.bootHeader === "ok", "radio version and classification");
  ok(f.sectorCrc32?.length === 2 && f.sectorCrc32.every((x) => x.crc32.length === 256), "the radio's sector CRCs, before and after");
  ok(f.flash?.length === 1 && f.flash[0].result === "sent" && f.flash[0].acked === f.flash[0].blocks && f.flash[0].eraseMs >= 0, "the install attempt");
  ok(f.verify?.bad.length === 0 && /^[0-9a-f]{64}$/.test(f.backup?.sha256 || "") && f.update?.size === UPDATE.length, "verify, backup hash, update size");
  ok(diag.counts.frameErrors >= 1, `the damaged frame is counted (${diag.counts.frameErrors})`);
  const leaks = firmwareInTrace(diag);
  ok(leaks.length === 0, `no firmware in the trace (${leaks.length} event(s)${leaks[0] ? `, first ${JSON.stringify(leaks[0]).slice(0, 120)}` : ""})`);
  ok(diag.trace.items().some((e) => e.k === "tx" && /^block \d+ of \d+$/.test(e.what || "")), "firmware blocks traced by label");
  ok(diag.trace.items().some((e) => e.k === "rx" && e.hex === "06") && diag.trace.items().some((e) => e.k === "rx" && e.hex?.startsWith("02240003")), "updater and boot ROM replies traced");
  const { by, size } = await diagFiles(diag);
  ok(/Install +sent, (\d+)\/\1 blocks/.test(by["summary.txt"]) && /Verify +all sectors match/.test(by["summary.txt"]), "summary has the install and the check");
  ok(size < 400000, `the zip is small (${size} bytes)`);
});

await scenario("diagnostics: Mac + Prolific over WebUSB, and a cable that dies mid-flash", async () => {
  const before = stockRadio();
  const radio = new MockRadio(before, { usbInfo: { usbVendorId: 0x067b, usbProductId: 0x2303 }, appleProlific: true, dieAfterBlocks: 100 });
  const { ui, seen } = person(radio);
  const { env } = macEnv(radio);
  const diag = new Recorder();
  env.diag = diag;
  await install(env, ui);
  ok(seen.done?.id === "installed", "finished after the retry");
  ok(/WebUSB/.test(diag.facts.cable?.path || "") && diag.facts.cable.usbVendorId === 0x067b, "cable path recorded");
  ok(diag.facts.reader?.baud === 115200 && diag.facts.reader.tries.length === 0, "no fast rates tried on Prolific");
  const fl = diag.facts.flash || [];
  ok(fl.length === 2 && fl[0].result === "failed" && fl[0].acked >= 99 && fl[0].acked < fl[0].blocks && fl[1].result === "sent", `both attempts (${fl.map((a) => `${a.result} ${a.acked}/${a.blocks}`).join(", ")})`);
  ok(firmwareInTrace(diag).length === 0, "no firmware in the trace");
});

await scenario("diagnostics: a refused radio (wrong firmware line) says why", async () => {
  const before = stockRadio();
  before.set(Buffer.from("DM32.01.L01.048"), VERSION_AT);
  const radio = new MockRadio(before);
  const { ui } = person(radio);
  const diag = new Recorder();
  diag.flowStart("install");
  try {
    await install(envFor(radio, { diag }), ui);
  } catch (e) {
    diag.flowEnd("refused", e);
  }
  ok(diag.facts.radio?.version === "DM32.01.L01.048", "the version the radio reports");
  const { by } = await diagFiles(diag);
  ok(/install .*refused .*\[version\]/.test(by["summary.txt"]) && /Radio +firmware "DM32.01.L01.048"/.test(by["summary.txt"]), "summary says it was refused for the version");
  ok(firmwareInTrace(diag).length === 0, "no firmware in the trace");
});

// --- the backup-file flows ---
await scenario("from backup: a radio on Kraken, with its stock backup", async () => {
  const backup = stockRadio();
  const radio = new MockRadio(simulateUpdate(backup, UPDATE));
  radio.flash[0x80000] ^= 1; // an older Kraken build, say
  const { ui, seen } = person(radio);
  await installFromBackup(envFor(radio), ui, backup);
  ok(same(radio.flash, simulateUpdate(backup, UPDATE)), "radio holds this Kraken build");
  ok(seen.checks["same-radio"]?.ok === true, "backup recognised as this radio's");
  ok(seen.checks.verify?.ok === true, "read-back check passed");
});

await scenario("restore stock from a backup", async () => {
  const backup = stockRadio();
  const radio = new MockRadio(simulateUpdate(backup, UPDATE));
  const { ui, seen } = person(radio);
  await restoreStock(envFor(radio), ui, backup);
  const flash = radio.flash;
  ok(same(flash.subarray(0, PATCH.stock.end), backup.subarray(0, PATCH.stock.end)), "radio holds the stock firmware again");
  ok(flash.subarray(Math.ceil(PATCH.stock.end / 1024) * 1024).every((b) => b === 0xff), "the rest is erased");
  ok(seen.done?.id === "stock-back", "finished");
});

await scenario("from backup: a backup of another radio is accepted with a warning", async () => {
  const backup = stockRadio();
  backup[UNIT + 7] ^= 0x5a;
  const radio = new MockRadio(stockRadio());
  const { ui, seen } = person(radio, { onAsk: { "verify": () => "secondary" } });
  await installFromBackup(envFor(radio), ui, backup);
  ok(seen.checks["same-radio"]?.ok === "warn", "warned that the backup is from another radio");
  ok(radio.flash.subarray(UNIT, UNIT + SECTOR).every((b, i) => b === ((i * 37 + 11) & 0xff)), "the radio's own per-unit sector is untouched");
});

await scenario("from backup: a backup that is not stock is refused before the radio is touched", async () => {
  const radio = new MockRadio(stockRadio());
  const { ui } = person(radio);
  await expectError(installFromBackup(envFor(radio), ui, simulateUpdate(stockRadio(), UPDATE)), "file-stock");
  ok(radio.readReplies === 0 && !radio.events.includes("erase"), "radio not read or written");
});

console.log(failures ? `\n${failures} FAILED` : "\nALL PASS");
process.exit(failures ? 1 : 0);
