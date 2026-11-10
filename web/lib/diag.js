// Diagnostics: everything the page sees, kept from page load so "Download diagnostics" can save a snapshot
// at any moment (docs/web-flasher.md, section 7). No DOM: the page (web/diagnostics.js) adds what it reads
// from the browser, and Node tests use the same code.
//
// What is kept:
//   - the log, the browser console, uncaught errors, the checks and each flow run with how it ended
//   - facts the flows learn (env.diag.note / env.diag.add): the cable, the reader, the radio's firmware
//     version, its sector CRCs, the classification, the install attempts
//   - a trace of the cable traffic (Link and kr.js feed it, see below)
//
// The trace never holds firmware. Bug reports are public, and nothing Kraken publishes may contain
// Baofeng's firmware. So:
//   - received bytes are kept only in 'raw' mode: the boot ROM's and the updater's handshakes (radio.js
//     switches modes). Once our reader runs ('framed'), only frame headers are kept (kr.js readFrame),
//     never DATA payloads.
//   - a sent block that carries image bytes is written with a label (radio.js) and kept as its length and
//     CRC-32 only.
// Nothing here holds the backup, the update file or the per-unit sector: only their hashes and CRCs.

import { hex } from "./bytes.js";
import { crc32 } from "./kr.js";

export const FORMAT = "kraken-diagnostics-1";
const RAW_EVENT_MAX = 64; // bytes of content kept per trace event
const COALESCE_MS = 20; // received bytes this close together join one event

// Keeps the first `head` items and the last `tail`, and counts what was dropped between them
class Ring {
  constructor(head, tail) {
    this.headMax = head;
    this.tailMax = tail;
    this.head = [];
    this.tail = [];
    this.at = 0;
    this.dropped = 0;
  }

  push(x) {
    if (this.head.length < this.headMax) return this.head.push(x);
    if (this.tail.length < this.tailMax) return this.tail.push(x);
    this.tail[this.at] = x;
    this.at = (this.at + 1) % this.tailMax;
    this.dropped++;
  }

  items() {
    return [...this.head, ...this.tail.slice(this.at), ...this.tail.slice(0, this.at)];
  }

  get length() {
    return this.head.length + this.tail.length;
  }
}

function errorInfo(e) {
  if (!e || typeof e !== "object") return { message: String(e) };
  const out = { name: e.name || e.constructor?.name, message: e.message };
  for (const k of ["code", "detail"]) if (e[k] !== undefined) out[k] = e[k];
  if (e.stack) out.stack = String(e.stack);
  return out;
}

function show(arg) {
  if (typeof arg === "string") return arg;
  if (arg instanceof Error) return `${arg.name}: ${arg.message}${arg.stack ? `\n${arg.stack}` : ""}`;
  try {
    return JSON.stringify(arg);
  } catch {
    return String(arg);
  }
}

const VENDORS = { 0x067b: "Prolific PL2303", 0x1a86: "WCH CH340/CH341", 0x0403: "FTDI", 0x10c4: "Silicon Labs CP210x" };
const h4 = (v) => (typeof v === "number" ? v.toString(16).padStart(4, "0") : "????");
export const usbName = (vid, pid) => `${h4(vid)}:${h4(pid)}${VENDORS[vid] ? ` (${VENDORS[vid]})` : ""}`;

const TYPES = { 0x80: "HELLO", 0x81: "PONG", 0x82: "OK", 0x83: "DATA", 0x84: "SUMS", 0x8f: "ERR" };
const STATUS_FRAMES = new Set([0x80, 0x81, 0x82, 0x8f]); // frames whose payload is ours, never flash

export class Recorder {
  constructor({ now, wall, traceHead = 3000, traceTail = 20000, logMax = 20000 } = {}) {
    this.now = now || (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
    this.wall = wall || (() => new Date());
    this.t0 = this.now();
    this.started = this.wall().toISOString();
    this.logs = new Ring(2000, logMax);
    this.consoleLines = new Ring(500, 4000);
    this.errors = [];
    this.facts = {};
    this.checks = [];
    this.flows = [];
    this.trace = new Ring(traceHead, traceTail);
    this.rxMode = "raw";
    this.counts = { txBytes: 0, rxBytes: 0, rxFramedBytes: 0, rxChunks: 0, frames: 0, frameErrors: 0, noiseBytes: 0, discarded: 0 };
    this.lastRx = null;
    this.screen = null;
  }

  t() {
    return Math.round((this.now() - this.t0) * 10) / 10;
  }

  // --- what the page and the flows report ---

  log(msg) {
    this.logs.push({ t: this.t(), wall: this.wall().toISOString(), msg: String(msg) });
  }

  console(level, args) {
    const text = Array.from(args, show).join(" ");
    this.consoleLines.push({ t: this.t(), level, text: text.length > 4000 ? `${text.slice(0, 4000)}...` : text });
  }

  error(kind, e) {
    if (this.errors.length < 200) this.errors.push({ t: this.t(), kind, ...errorInfo(e) });
  }

  note(key, value) {
    this.facts[key] = value;
  }

  add(key, value) {
    (this.facts[key] ||= []).push({ t: this.t(), ...value });
  }

  check(id, label, ok, detail) {
    this.checks.push({ t: this.t(), id, label, ok, ...(detail ? { detail } : {}) });
  }

  flowStart(name) {
    this.flows.push({ name, t: this.t(), wall: this.wall().toISOString(), outcome: "running" });
    this.event(`flow ${name} started`);
  }

  flowEnd(outcome, e) {
    const f = this.flows[this.flows.length - 1];
    if (!f || f.outcome !== "running") return;
    f.outcome = outcome;
    f.ms = Math.round(this.t() - f.t);
    if (e) f.error = errorInfo(e);
    this.event(`flow ${f.name} ended: ${outcome}`);
  }

  setScreen(s) {
    this.screen = { t: this.t(), ...s };
  }

  // --- the trace (Link, kr.js and radio.js call these) ---

  push(e) {
    this.lastRx = null;
    this.trace.push(e);
  }

  event(text) {
    this.push({ t: this.t(), k: "ev", text });
  }

  mode(m) {
    if (m === this.rxMode) return;
    this.rxMode = m;
    this.event(`trace mode ${m}${m === "framed" ? " (received bytes now shown as frame headers only)" : ""}`);
  }

  tx(bytes, baud, what) {
    this.counts.txBytes += bytes.length;
    if (what) this.push({ t: this.t(), k: "tx", baud, n: bytes.length, what, crc: crc32(bytes) });
    else this.push({ t: this.t(), k: "tx", baud, n: bytes.length, hex: hex(bytes.subarray(0, RAW_EVENT_MAX)) });
  }

  rx(bytes, baud) {
    this.counts.rxBytes += bytes.length;
    this.counts.rxChunks++;
    if (this.rxMode !== "raw") {
      this.counts.rxFramedBytes += bytes.length;
      return;
    }
    const t = this.t();
    const last = this.lastRx;
    if (last && last.baud === baud && t - last.tEnd <= COALESCE_MS && last.n + bytes.length <= RAW_EVENT_MAX) {
      last.hex += hex(bytes);
      last.n += bytes.length;
      last.tEnd = t;
      return;
    }
    const e = { t, tEnd: t, k: "rx", baud, n: bytes.length, hex: hex(bytes.subarray(0, RAW_EVENT_MAX)) };
    this.push(e);
    this.lastRx = e;
  }

  // One reader frame as kr.js parsed it. Payloads are kept only for the short status frames.
  frame({ type, len, ok = true, payload, noise = 0, why }) {
    this.counts.frames++;
    this.counts.noiseBytes += noise;
    if (!ok) this.counts.frameErrors++;
    const e = { t: this.t(), k: "frame", type, len, ok };
    if (noise) e.noise = noise;
    if (why) e.why = why;
    if (ok && payload) {
      if (type === 0x83) e.addr = payload[0] | (payload[1] << 8) | (payload[2] << 16) | (payload[3] << 24);
      else if (type === 0x84) e.sums = payload.length / 4;
      else if (STATUS_FRAMES.has(type)) e.hex = hex(payload.subarray(0, RAW_EVENT_MAX));
    }
    this.push(e);
  }

  discarded(n) {
    this.counts.discarded += n;
    if (n) this.event(`discarded ${n} received byte(s)`);
  }

  // --- the files in the zip ---

  traceText() {
    const lines = [];
    const items = this.trace.items();
    const gapAt = this.trace.head.length;
    items.forEach((e, i) => {
      if (i === gapAt && this.trace.dropped) lines.push(`         ... ${this.trace.dropped} event(s) dropped here (the trace keeps the first ${this.trace.headMax} and the last ${this.trace.tailMax}) ...`);
      const t = `+${(e.t / 1000).toFixed(3).padStart(9)}`;
      if (e.k === "ev") lines.push(`${t}  --  ${e.text}`);
      else if (e.k === "frame") {
        const name = TYPES[e.type] || `0x${(e.type ?? 0).toString(16)}`;
        let s = `${t}  <=  frame ${name} len ${e.len}${e.ok ? "" : ` BAD (${e.why || "?"})`}`;
        if (e.addr !== undefined) s += ` addr 0x${(e.addr >>> 0).toString(16)}`;
        if (e.sums !== undefined) s += ` (${e.sums} CRCs)`;
        if (e.hex) s += `  ${spaced(e.hex)}`;
        if (e.noise) s += `  [${e.noise} noise byte(s) skipped before it]`;
        lines.push(s);
      } else {
        const dir = e.k === "tx" ? "->" : "<-";
        const body = e.what ? `[${e.what}] crc32 ${(e.crc >>> 0).toString(16).padStart(8, "0")}` : spaced(e.hex) + (e.n > e.hex.length / 2 ? ` ...` : "");
        lines.push(`${t}  ${dir}  ${String(e.baud ?? "?").padStart(7)}  ${String(e.n).padStart(5)} B  ${body}`);
      }
    });
    return [
      "Kraken installer: cable trace",
      "",
      "Time is seconds since the page loaded. -> sent to the radio, <- received, <= a reader frame, -- an event.",
      "Received bytes are shown in full only for the boot ROM and the updater. While our reader runs, only frame",
      "headers are kept, so no firmware is ever in this file. Blocks that carry firmware are shown by length",
      "and CRC-32 only.",
      "",
      `Totals: sent ${this.counts.txBytes} B, received ${this.counts.rxBytes} B in ${this.counts.rxChunks} chunks ` +
        `(${this.counts.rxFramedBytes} B as frames), ${this.counts.frames} frames, ${this.counts.frameErrors} bad, ` +
        `${this.counts.noiseBytes} noise bytes, ${this.counts.discarded} B discarded.`,
      "",
      ...lines,
      "",
    ].join("\n");
  }

  logText() {
    const items = this.logs.items();
    return [
      "Kraken installer: log",
      "Columns: seconds since the page loaded, UTC time, message.",
      ...(this.logs.dropped ? [`(${this.logs.dropped} middle line(s) dropped)`] : []),
      "",
      ...items.map((e) => `+${(e.t / 1000).toFixed(3).padStart(9)}  ${e.wall.slice(11, 23)}  ${e.msg}`),
      "",
    ].join("\n");
  }

  consoleText() {
    const lines = this.consoleLines.items().map((e) => `+${(e.t / 1000).toFixed(3).padStart(9)}  ${e.level.padEnd(5)}  ${e.text}`);
    const errs = this.errors.map((e) => `+${(e.t / 1000).toFixed(3).padStart(9)}  ${e.kind}  ${e.name || ""}: ${e.message}${e.stack ? `\n${e.stack}` : ""}`);
    return ["Kraken installer: browser console and uncaught errors", "", ...lines, "", "Uncaught errors:", ...(errs.length ? errs : ["(none)"]), ""].join("\n");
  }

  data(snapshot = {}) {
    return {
      format: FORMAT,
      created: this.wall().toISOString(),
      pageLoaded: this.started,
      uptimeMs: Math.round(this.t()),
      ...snapshot,
      screen: this.screen,
      flows: this.flows,
      checks: this.checks,
      facts: this.facts,
      errors: this.errors,
      counts: this.counts,
      traceEvents: this.trace.length,
      traceDropped: this.trace.dropped,
    };
  }

  summaryText(d) {
    const L = [];
    const row = (k, v) => v !== undefined && v !== null && v !== "" && L.push(`${k.padEnd(13)}${v}`);
    const f = this.facts;
    L.push("Kraken installer diagnostics", "");
    row("Created", `${d.created} (page open ${Math.round(d.uptimeMs / 1000)} s)`);
    const pg = d.page || {};
    row("Page", [pg.release || "unknown release", pg.url].filter(Boolean).join(", "));
    if (pg.patch) row("Build", `firmware ${pg.patch.firmware}, update ${pg.patch.update}, reader ${pg.patch.reader}`);
    else row("Build", pg.patchError ? `NOT LOADED: ${pg.patchError}` : "not loaded");
    const b = d.browser || {};
    row("Browser", b.summary || b.userAgent);
    row("OS", b.os);
    const feat = d.features || {};
    row("Features", Object.entries(feat).map(([k, v]) => `${k} ${v ? "yes" : "NO"}`).join(", "));
    const ports = d.serialPorts;
    if (Array.isArray(ports)) row("Serial ports", ports.length ? ports.map((p) => `${usbName(p.usbVendorId, p.usbProductId)}${p.connected === false ? " disconnected" : ""}${p.open ? " open" : ""}`).join("; ") : "none allowed yet");
    const usb = d.usbDevices;
    if (Array.isArray(usb) && usb.length) row("USB devices", usb.map((u) => `${usbName(u.vendorId, u.productId)} "${u.productName || ""}" rev ${u.deviceVersion}`).join("; "));
    L.push("");
    for (const fl of this.flows) row("Flow", `${fl.name} at ${fl.wall.slice(11, 19)} UTC: ${fl.outcome}${fl.ms !== undefined ? ` after ${Math.round(fl.ms / 1000)} s` : ""}${fl.error ? ` - ${fl.error.name}${fl.error.code ? ` [${fl.error.code}]` : ""}: ${fl.error.message}` : ""}`);
    if (!this.flows.length) row("Flow", "none started");
    if (this.screen) row("Screen", `${this.screen.id || "?"}: ${this.screen.title || ""}${this.screen.status ? ` (${this.screen.status})` : ""}`);
    if (f.cable) row("Cable", `${usbName(f.cable.usbVendorId, f.cable.usbProductId)} via ${f.cable.path}`);
    if (f.reader) {
      const tries = (f.reader.tries || []).map((x) => `${x.baud} ${x.result}`).join(", ");
      row("Reader", `v${f.reader.version}, ROM divisor ${f.reader.divisor}, ran at ${f.reader.baud ?? "?"} baud${tries ? ` (tried ${tries})` : ""}`);
    }
    if (f.radio) row("Radio", `firmware "${f.radio.version}"${f.radio.versionAt ? ` at ${f.radio.versionAt}` : ""},${f.radio.kind || "not classified"}${f.radio.differ ? `, ${f.radio.differ} sector(s) differ from stock` : ""}`);
    if (f.backupFile) row("Backup file", `${f.backupFile.size} B, ${f.backupFile.version || "no version"}, ${f.backupFile.kind || "?"}`);
    for (const a of f.flash || []) row("Install", `${a.result}${a.blocks ? `, ${a.acked}/${a.blocks} blocks` : ""}${a.code ? ` [${a.code}]` : ""}${a.message ? `: ${a.message}` : ""}`);
    if (f.verify) row("Verify", f.verify.bad.length ? `${f.verify.bad.length} sector(s) differ, first ${f.verify.bad[0]}` : "all sectors match");
    const failed = this.checks.filter((c) => c.ok !== true);
    row("Checks", `${this.checks.length} run${failed.length ? `; not ok: ${failed.map((c) => `${c.id} (${c.ok === false ? "failed" : "warning"}${c.detail ? `: ${c.detail}` : ""})`).join("; ")}` : ", all ok"}`);
    if (this.errors.length) row("Page errors", `${this.errors.length}, last: ${this.errors[this.errors.length - 1].message}`);
    row("Trace", `${this.trace.length} events${this.trace.dropped ? ` (${this.trace.dropped} dropped)` : ""}, ${this.counts.frameErrors} bad frame(s)`);
    L.push("", "Details: log.txt (what the page did), trace.txt (cable traffic), diagnostics.json (everything).", "");
    return L.join("\n");
  }

  // The files of the diagnostics zip. snapshot: what the page read from the browser just now.
  files(snapshot = {}) {
    const d = this.data(snapshot);
    return [
      { name: "README.txt", data: README },
      { name: "summary.txt", data: this.summaryText(d) },
      { name: "diagnostics.json", data: JSON.stringify(d, null, 1) + "\n" },
      { name: "log.txt", data: this.logText() },
      { name: "trace.txt", data: this.traceText() },
      { name: "console.txt", data: this.consoleText() },
    ];
  }
}

function spaced(h) {
  return h.replace(/(..)(?=.)/g, "$1 ");
}

const README = `Kraken web installer diagnostics

This file was saved by the Kraken web installer when "Download diagnostics" was clicked. Attach it,
unchanged, to a bug report: https://github.com/awkaplan/dm32uv-kraken/issues/new?template=web_installer.yml

What is in it
  summary.txt       the short version: page, browser, cable, radio, what happened
  diagnostics.json  everything below, as data
  log.txt           every step the page took, with times
  trace.txt         the bytes sent and received on the cable (see below)
  console.txt       the browser console and any page errors

  It includes: the page's release, your browser and operating system versions (from the browser's
  user-agent data), your screen size, language and time-zone offset, which browser features exist,
  the USB IDs and names of the cables this page was allowed to use, and what the page learned about
  the radio: its firmware version, a CRC-32 of each 4 KB flash sector, and the SHA-256 of any backup.

What is not in it
  No firmware, no backup file, no update file and no codeplug (channels, contacts, settings). While the
  page reads the radio, the trace keeps only frame headers, and firmware blocks sent to the radio are
  shown as their length and CRC-32. No USB serial numbers.

  It may include the name of a backup file you chose, which is in the log. Read summary.txt and log.txt
  before posting if you want to check. Bug reports on GitHub are public.
`;
