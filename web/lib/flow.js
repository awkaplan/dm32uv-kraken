// The installer's three flows. They talk to the person through a `ui` object (app.js in the browser, a
// scripted stand-in in tests/web_flasher_test.mjs), so the same code runs in both:
//
//   ui.phase(id)                       'check' | 'backup' | 'save' | 'install' | 'verify' | 'done'
//   ui.check(id, label, ok, detail)    one row of the checklist (ok: true, false, or 'warn')
//   ui.status(text) / ui.progress(fraction | null, label) / ui.log(text)
//   ui.show({ id, icon, title, steps, note })   an instruction with no button (the page waits on the radio)
//   ui.ask({ id, icon, title, steps, note, button, secondary })   -> 'primary' | 'secondary'
//   ui.saveBackup(bytes, name)         resolves once the backup is saved
//   ui.done({ id, title, steps })
//   `id` names the screen for the tests; titles and text are free to change. Keep the text short: one
//   instruction per line, no explanations the person does not need to act.
//   ui.lockCancel(on)                  optional: true while the firmware area is being replaced
//
// env: { bundle, reader, requestPort(), log } (bundle: image.js loadPatch of kraken_patch.json, one entry per stock
// firmware build; it and the reader are loaded and checked before any flow). Each flow sets env.patch to the build it
// works on: the radio's (install) or the backup's (from a backup, and the radio must then be on the same build). And
// for the Mac's Prolific cables: platform ('mac' | 'other') and usbDevice(filter, prompt) -> a WebUSB device.
// env.diag (optional): a lib/diag.js Recorder. It gets the cable traffic and the facts below (never flash
// contents), for the page's "Download diagnostics".
//
// Our reader on the radio can CRC flash itself, so the slow part (reading 1 MB) happens once, for the
// backup. Telling stock from not-stock, checking the backup read and checking the install afterwards
// all compare per-sector CRC-32s, which takes seconds.

import { addr, equal, sha256 } from "./bytes.js";
import {
  APP, FLASH_SIZE, SECTOR, UNIT, BOOT_MAGIC, CheckError,
  classify, krakenUpdate, quickChecks, stockUpdate, expectedFlash, findVersion, isFirmware, wrongVersionMessage,
  selectBuild, versionSpots,
} from "./image.js";
import { Link } from "./link.js";
import { crc32 } from "./kr.js";
import { RadioError, startReader, speedUp, flashUpdate, cableMaxBaud } from "./radio.js";
import { Pl2303Port, PROLIFIC_VENDOR } from "./pl2303.js";

const BASE_BAUD = 115200;

// Facts for the diagnostics download (env.diag): note replaces, merge adds fields, add appends to a list
const note = (env, key, value) => env.diag?.note(key, value);
const merge = (env, key, value) => env.diag && env.diag.note(key, { ...(env.diag.facts[key] || {}), ...value });
const add = (env, key, value) => env.diag?.add(key, value);
const crcHex = (list) => list.map((c) => c.toString(16).padStart(8, "0"));

// The port and the current link on it. The link is reopened to change the baud rate.
class Session {
  constructor(env, port) {
    this.env = env;
    this.port = port;
    this.log = env.log;
    this.link = null;
  }

  async open(baud = BASE_BAUD) {
    this.link = new Link(this.port, { log: this.log, trace: this.env.diag });
    await this.link.open(baud);
    this.env.link = this.link;
    return this.link;
  }

  async reopen(baud) {
    if (this.link) await this.link.close();
    return this.open(baud);
  }

  close() {
    return this.link ? this.link.close() : null;
  }
}

// On a Mac, Apple's driver for Prolific PL2303 cables runs the line at 9600 baud whatever a browser asks
// for (pl2303.js has the story), so there the page drives the cable itself over WebUSB. That is a second
// permission, asked for once; if it cannot be had, the serial port is tried anyway (another driver may be
// installed).
async function openSession(env, ui) {
  const port = await env.requestPort();
  const info = port.getInfo ? port.getInfo() : {};
  if (env.platform === "mac" && info.usbVendorId === PROLIFIC_VENDOR && env.usbDevice) {
    const filter = { vendorId: info.usbVendorId, productId: info.usbProductId };
    let dev = await env.usbDevice(filter, false);
    if (!dev) {
      await ui.ask({
        id: "usb-access",
        icon: "usb",
        title: "Allow access to the cable",
        steps: ["Click Continue, pick \u201cUSB-Serial Controller\u201d, then click Connect."],
        note: "Macs need this one extra step for this cable.",
        button: "Continue",
      });
      dev = await env.usbDevice(filter, true);
    }
    if (dev) {
      const direct = new Session(env, new Pl2303Port(dev));
      try {
        await direct.open();
        env.log("Prolific cable on a Mac: using it directly over USB");
        note(env, "cable", { usbVendorId: info.usbVendorId, usbProductId: info.usbProductId, path: "WebUSB (direct, Apple driver bypassed)" });
        return direct;
      } catch (e) {
        env.log(`direct USB access to the cable failed (${e.message}); trying its serial port`);
        await direct.close();
      }
    }
  }
  const s = new Session(env, port);
  s.appleProlific = env.platform === "mac" && info.usbVendorId === PROLIFIC_VENDOR;
  note(env, "cable", { usbVendorId: info.usbVendorId, usbProductId: info.usbProductId, path: s.appleProlific ? "Web Serial (Apple's Prolific driver)" : "Web Serial" });
  await s.open();
  return s;
}

function sectorSums(img) {
  const out = [];
  for (let s = 0; s < FLASH_SIZE; s += SECTOR) out.push(crc32(img.subarray(s, s + SECTOR)));
  return out;
}

// Power on, load our reader, go as fast as the cable allows, and check boot header and version.
async function connect(session, env, ui, { askOff = true } = {}) {
  if (askOff) {
    await ui.ask({ id: "off", icon: "power", title: "Turn the radio off", steps: ["Leave the cable plugged in."], button: "It\u2019s off" });
  }
  if (session.link.baud !== BASE_BAUD) await session.reopen(BASE_BAUD);
  ui.show({
    id: "on",
    icon: "power",
    title: "Turn the radio on",
    steps: ["Turn it on normally. Don\u2019t hold any keys."],
    note: "The screen may stay dark. That\u2019s normal.",
  });
  ui.progress(null, "Waiting for the radio\u2026");
  let reader;
  try {
    reader = await startReader(session.link, env.reader, env.bootWaitMs ? { waitMs: env.bootWaitMs } : {});
  } catch (e) {
    if (e.code === "no-boot" && session.appleProlific) {
      throw new RadioError("no-boot", "The radio didn\u2019t answer. Close any other program using the cable, try again, and allow access to the cable when asked.");
    }
    throw e;
  }
  const usb = session.port.getInfo ? session.port.getInfo() : {};
  const hex4 = (v) => (v === undefined ? "?" : v.toString(16).padStart(4, "0"));
  const baud = await speedUp(session, reader, { maxBaud: cableMaxBaud(usb) });
  ui.log(`cable ${hex4(usb.usbVendorId)}:${hex4(usb.usbProductId)}`);
  ui.log(`reader v${reader.version} running at ${baud} baud`);
  note(env, "reader", { version: reader.version, divisor: reader.divisor, baud, maxBaud: cableMaxBaud(usb), tries: reader.speedTries || [] });
  const head = new Uint8Array(FLASH_SIZE);
  await reader.read(head, 0, SECTOR);
  // The sector of each build's version spot (and of known unsupported builds', to name them in a refusal)
  const spots = versionSpots(env.bundle);
  for (const s of new Set(spots.map((a) => a & ~(SECTOR - 1)))) await reader.read(head, s, SECTOR);
  const radioBuild = selectBuild(env.bundle, head);
  if (!env.patch) env.patch = radioBuild;          // install: the radio's build; from a backup: the backup's already
  const checks = quickChecks(head, env.patch, env.bundle);
  const found = findVersion(head, spots);
  merge(env, "radio", {
    version: found.version,
    versionAt: found.at >= 0 ? addr(found.at) : undefined,
    bootHeader: equal(head.subarray(0, 4), BOOT_MAGIC) ? "ok" : "not the DM-32UV boot magic",
  });
  for (const c of checks) ui.check(c.id, c.label, c.ok, c.ok ? undefined : c.detail);
  const bad = checks.find((c) => !c.ok);
  if (bad) {
    if (bad.id === "version") {
      if (radioBuild && env.patch && radioBuild !== env.patch) {
        throw new CheckError("version", `This radio has ${radioBuild.firmware}, but that backup is of ${env.patch.firmware}. Use a backup of this radio. Nothing was changed.`);
      }
      throw new CheckError("version", `${wrongVersionMessage(found.version, env.bundle, "This radio")} Nothing was changed.`);
    }
    throw new CheckError(bad.id, "This doesn\u2019t look like a DM-32UV. Nothing was changed.");
  }
  ui.progress(null, "Checking the radio\u2026");
  const sums = await reader.sums(0, FLASH_SIZE, SECTOR);
  add(env, "sectorCrc32", { when: askOff ? "connect" : "after install", unitSector: crcHex([sums[UNIT / SECTOR]])[0], crc32: crcHex(sums) });
  return { reader, sums };
}

// From the radio's sector CRCs alone: 'stock', 'kraken' (this build), 'maybe-stock' (only the sector
// where stock ends differs: padding from an earlier update), or 'other' with the differing sectors.
function quickClassify(sums, patch) {
  const first = APP / SECTOR;
  const t = patch.target.sector_crc32, st = patch.stock.sector_crc32;
  if (t.every((c, i) => sums[first + i] === c)) return { kind: "kraken" };
  const bad = [];
  st.forEach((c, i) => sums[first + i] !== c && bad.push(APP + i * SECTOR));
  if (!bad.length) return { kind: "stock" };
  const endSector = (patch.stock.end - 1) & ~(SECTOR - 1);
  if (bad.length === 1 && bad[0] === endSector) return { kind: "maybe-stock" };
  return { kind: "other", bad };
}

function refuseNotStock(env, ui, n) {
  ui.check("stock", "Firmware is stock", false, `${n} of ${env.patch.stock.sector_crc32.length} firmware sectors differ from stock.`);
  throw new CheckError("not-stock",
    "This radio\u2019s firmware has been changed, so it can\u2019t be backed up as stock. " +
    "If it runs an older Kraken, use \u201cUpdate or remove Kraken\u201d below. Nothing was changed.");
}

// Read all of program flash and check every sector against the radio's own CRC
async function backup(reader, sums, ui, env) {
  const img = new Uint8Array(FLASH_SIZE);
  ui.show({
    id: "backup",
    icon: "download",
    title: "Backing up your radio",
    steps: [reader.baud > BASE_BAUD ? "About 15 seconds." : "About a minute and a half."],
    note: "Keep the radio on.",
  });
  await reader.read(img, 0, FLASH_SIZE, (f) => ui.progress(f, "Backing up"));
  for (let pass = 0; pass < 3; pass++) {
    const bad = [];
    for (let s = 0; s < FLASH_SIZE; s += SECTOR) if (crc32(img.subarray(s, s + SECTOR)) !== sums[s / SECTOR]) bad.push(s);
    if (!bad.length) {
      ui.progress(1, "Backed up");
      merge(env, "backup", { rereadPasses: pass, sha256: await sha256(img), firmwareSha256: await sha256(img.subarray(APP, env.patch.stock.end)) });
      return img;
    }
    add(env, "backupRereads", { sectors: bad.map(addr) });
    ui.log(`sector(s) ${bad.map(addr).join(" ")} do not match the radio's CRC; reading again`);
    for (const s of bad) await reader.read(img, s, SECTOR);
  }
  throw new RadioError("read-unstable", "The backup didn\u2019t read cleanly. Plug the cable into another USB port and try again.");
}

async function checkBackupFile(img, env, ui) {
  const spots = versionSpots(env.bundle);
  note(env, "backupFile", { size: img.length, sha256: await sha256(img), version: img.length === FLASH_SIZE ? findVersion(img, spots).version : undefined });
  const ok = img.length === FLASH_SIZE && equal(img.subarray(0, 4), BOOT_MAGIC);
  ui.check("file", "File is a 1 MB DM-32UV program-memory backup", ok);
  if (!ok) throw new CheckError("file", "That file isn\u2019t a DM-32UV backup from this page.");
  const v = findVersion(img, spots).version;
  env.patch = selectBuild(env.bundle, img);
  const vok = !!env.patch;
  ui.check("file-version", vok ? `Backup is of ${env.patch.firmware}` : "Backup is of a firmware Kraken supports", vok,
    vok ? undefined : `The backup reports ${v || "no version"}.`);
  if (!vok) throw new CheckError("file-version", wrongVersionMessage(v, env.bundle, "That backup"));
}

// Flash FILE with the updater, then check it by sector CRCs. Loops on failure: the updater can always be
// entered again. low: the radio's flash below 0x0300C000 if known (the updater never writes there).
async function flashAndVerify(session, env, ui, file, { low, what }) {
  // Kraken on a build that has not been tested on a real radio yet (firmware/firmwares.json radio_tested): say so, once,
  // and go on only if the person accepts the risk. Putting stock back needs no warning.
  if (what === "Kraken" && env.patch.radio_tested !== true) {
    const go = await ui.ask({
      id: "untested-firmware",
      icon: "alert",
      title: `Kraken on ${env.patch.firmware} is unstable`,
      steps: [
        "It has been tested only in an emulator, not on a real radio. It may not work.",
        "Install it at your own risk.",
        "Your backup can put stock firmware back: Update or remove Kraken > Restore stock firmware.",
      ],
      button: "Install at my own risk",
      secondary: "Cancel",
    });
    note(env, "untestedWarning", { firmware: env.patch.firmware, accepted: go !== "secondary" });
    if (go === "secondary") throw new CheckError("stopped", "Stopped. Nothing was written to the radio.");
  }
  for (;;) {
    ui.phase("install");
    await ui.ask({
      id: "update-mode",
      icon: "keys",
      title: "Start the radio in update mode",
      steps: ["Turn the radio off.", "Hold SK1 and PTT, and turn it on.", "Let go when the light turns green."],
      button: `Install ${what}`,
    });
    if (session.link.baud !== BASE_BAUD) await session.reopen(BASE_BAUD); // the updater runs at 115200
    ui.lockCancel?.(true);
    ui.show({ id: "installing", icon: "install", title: `Installing ${what}`, steps: ["About a minute and a half."], note: "Don\u2019t unplug the cable." });
    ui.progress(null, "Erasing\u2026");
    const stats = {};
    try {
      await flashUpdate(session.link, file, {
        onPhase: (p) => p === "restart" && ui.progress(null, "Restarting the radio\u2026"),
        onProgress: (f) => ui.progress(f, "Installing"),
        stats,
      });
      add(env, "flash", { what, result: "sent", ...stats });
    } catch (e) {
      ui.lockCancel?.(false);
      add(env, "flash", { what, result: "failed", code: e.code, message: e.message, ...stats });
      if (!(e instanceof RadioError)) throw e;
      ui.log(`flash: ${e.message}`);
      const noUpdater = e.code === "no-updater";
      const again = await ui.ask({
        id: noUpdater ? "no-updater" : "flash-failed",
        icon: "alert",
        title: noUpdater ? "The radio isn\u2019t in update mode" : "The install didn\u2019t finish",
        steps: noUpdater
          ? ["Turn it off. Hold SK1 and PTT, turn it on, and let go when the light turns green."]
          : ["Your radio is safe. Start it in update mode again: hold SK1 and PTT while turning it on."],
        button: "Try again",
        secondary: "Stop",
      });
      if (again === "secondary") throw new CheckError("stopped", "Stopped. To finish, start the radio in update mode and run the installer again.");
      continue;
    }
    ui.lockCancel?.(false);
    ui.check("flash", `${what} sent to the radio`, true);

    ui.phase("verify");
    const choice = await ui.ask({
      id: "verify",
      icon: "power",
      title: "Turn the radio off",
      steps: ["Wait for it to restart (about 10 seconds), then turn it off."],
      note: "Next, the page checks the install. It takes a few seconds.",
      button: "It\u2019s off",
      secondary: "Skip check",
    });
    if (choice === "secondary") {
      ui.check("verify", "The radio holds exactly what was sent", "warn", "Skipped.");
      return;
    }
    const { sums } = await connect(session, env, ui, { askOff: false });
    const want = sectorSums(expectedFlash(file, low));
    const bad = [];
    for (let s = low ? 0 : APP; s < FLASH_SIZE; s += SECTOR) if (sums[s / SECTOR] !== want[s / SECTOR]) bad.push(s);
    note(env, "verify", { bad: bad.map(addr) });
    if (!bad.length) {
      ui.check("verify", "The radio holds exactly what was sent", true);
      return;
    }
    ui.log(`after the install, sectors ${bad.map(addr).join(" ")} differ`);
    ui.check("verify", "The radio holds exactly what was sent", false, `${bad.length} sector(s) differ, first at ${addr(bad[0])}.`);
    const again = await ui.ask({
      id: "verify-failed",
      icon: "alert",
      title: "The check found a problem",
      steps: ["Install again to fix it."],
      button: "Install again",
      secondary: "Stop",
    });
    if (again === "secondary") throw new CheckError("verify", "The install didn\u2019t check out. Install again before using the radio.");
  }
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

const DONE_KRAKEN = [
  "Turn the radio off and on again.",
  "Kraken\u2019s features are in the new KRAKEN menu, at the end of the main menu.",
  "Keep your backup file. It\u2019s your way back to stock firmware.",
];

// The one-button flow: check, back up, save, patch, flash, check again
export async function install(env, ui) {
  env.patch = null;                                // chosen from the radio when it connects
  const session = await openSession(env, ui);
  try {
    ui.phase("check");
    const { reader, sums } = await connect(session, env, ui);
    const quick = quickClassify(sums, env.patch);
    merge(env, "radio", { kind: quick.kind, differ: quick.bad?.length, differing: quick.bad?.slice(0, 64).map(addr) });
    if (quick.kind === "kraken") {
      ui.check("stock", "Firmware is stock", "warn", "This radio already runs this Kraken build.");
      ui.done({ id: "already", title: "Kraken is already installed", steps: ["This radio already has this version.", "Turn it off and on again to use it."] });
      return;
    }
    if (quick.kind === "other") refuseNotStock(env, ui, quick.bad.length);
    ui.phase("backup");
    const img = await backup(reader, sums, ui, env);
    if ((await classify(img, env.patch)) !== "stock") refuseNotStock(env, ui, 1);
    ui.check("stock", "Firmware is stock and the backup is exact", true);
    ui.phase("save");
    await ui.saveBackup(img, `DM32UV-backup-${stamp()}.bin`);
    ui.check("saved", "Backup saved", true);
    const file = await krakenUpdate(img, env.patch);
    note(env, "update", { what: "Kraken", size: file.length, sha256: await sha256(file) });
    ui.check("update", "Kraken update built and checked", true);
    await flashAndVerify(session, env, ui, file, { low: img.subarray(0, APP), what: "Kraken" });
    ui.phase("done");
    ui.done({ id: "installed", title: "Kraken is installed", steps: DONE_KRAKEN });
  } finally {
    await session.close();
  }
}

async function fileFlow(env, ui, backupImg, { makeFile, what, done }) {
  env.patch = null;                                // chosen from the backup
  const session = await openSession(env, ui);
  try {
    ui.phase("check");
    await checkBackupFile(backupImg, env, ui);
    const kind = await classify(backupImg, env.patch);
    merge(env, "backupFile", { kind });
    ui.check("file-stock", "Backup holds stock firmware", kind === "stock", kind === "stock" ? undefined : `It is not stock ${env.patch.firmware} (maybe a backup taken after installing Kraken).`);
    if (kind !== "stock") throw new CheckError("file-stock", "That backup isn\u2019t of stock firmware. Choose the one this page saved before Kraken was first installed.");
    const file = await makeFile(backupImg);
    note(env, "update", { what, size: file.length, sha256: await sha256(file) });
    ui.check("update", `${what[0].toUpperCase() + what.slice(1)} update built and checked`, true);
    const { sums } = await connect(session, env, ui);
    const same = sums[UNIT / SECTOR] === crc32(backupImg.subarray(UNIT, UNIT + SECTOR));
    merge(env, "backupFile", { sameRadio: same });
    ui.check("same-radio", "The backup is from this radio", same ? true : "warn",
      same ? undefined : "Its per-unit sector differs. That is harmless here (the updater never writes it), but label your backups so you do not mix up radios.");
    await flashAndVerify(session, env, ui, file, { low: null, what });
    ui.phase("done");
    ui.done(done);
  } finally {
    await session.close();
  }
}

// Kraken from a backup saved earlier (for a radio that already runs an earlier Kraken build)
export function installFromBackup(env, ui, backupImg) {
  return fileFlow(env, ui, backupImg, { makeFile: (b) => krakenUpdate(b, env.patch), what: "Kraken", done: { id: "installed", title: "Kraken is installed", steps: DONE_KRAKEN } });
}

// Stock firmware back from a backup
export function restoreStock(env, ui, backupImg) {
  return fileFlow(env, ui, backupImg, {
    makeFile: async (b) => stockUpdate(b, env.patch),
    what: "stock firmware",
    done: { id: "stock-back", title: "Stock firmware is back", steps: ["Turn the radio off and on again."] },
  });
}
