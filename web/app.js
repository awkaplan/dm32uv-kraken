// The installer page: the page-load checks, and the `ui` the flows in lib/flow.js talk to.
// One screen at a time: a title, one or a few short instructions, at most two buttons.

import { sha256, equal } from "./lib/bytes.js";
import { loadPatch, CheckError } from "./lib/image.js";
import { CancelledError } from "./lib/link.js";
import { install, installFromBackup, restoreStock } from "./lib/flow.js";

const $ = (id) => document.getElementById(id);
const env = { patch: null, reader: null, link: null, log };
env.platform = /mac/i.test(navigator.userAgentData?.platform || navigator.platform || "") ? "mac" : "other";
// A WebUSB device this page may use: one already allowed, else (prompt) the browser's picker, which needs
// a click just before. Only the Mac's Prolific cables need it (lib/pl2303.js).
env.usbDevice = async (filter, prompt) => {
  if (!navigator.usb) return null;
  const match = (d) => d.vendorId === filter.vendorId && d.productId === filter.productId;
  return (await navigator.usb.getDevices()).find(match) || (prompt ? navigator.usb.requestDevice({ filters: [filter] }) : null);
};
let running = false;
let backupBytes = null;

// --- icons (24px line icons, drawn here so the page needs nothing else) ---

const ICONS = {
  radio: '<path d="M8 2v6"/><rect x="5" y="8" width="14" height="14" rx="2.5"/><rect x="8" y="11" width="8" height="4" rx="1"/><path d="M9 18.5h.01M12 18.5h.01M15 18.5h.01"/>',
  usb: '<path d="M9 2v4M15 2v4"/><rect x="6" y="6" width="12" height="7" rx="1.5"/><path d="M12 13v3a3 3 0 0 1-3 3H7"/>',
  battery: '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/><path d="M5.5 10v4M9 10v4M12.5 10v4"/>',
  browser: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M6.5 6.5h.01M9 6.5h.01"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  power: '<path d="M12 3v8"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
  download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
  save: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/><path d="M12 11v6"/><path d="m9 14 3 3 3-3"/>',
  keys: '<circle cx="12" cy="12" r="9"/><path d="M12 16.5v-9"/><path d="m8 11.5 4-4 4 4"/>',
  install: '<path d="M12 3v10"/><path d="m8 9 4 4 4-4"/><rect x="4" y="15" width="16" height="6" rx="1.5"/>',
  wait: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5v.5"/>',
};

function svg(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

for (const el of document.querySelectorAll("[data-icon]")) el.innerHTML = svg(el.dataset.icon);

// --- log (behind "Show log") ---

function log(msg) {
  const t = new Date().toISOString().slice(11, 19);
  $("log").textContent += `${t}  ${msg}\n`;
  console.log(`[kraken] ${msg}`);
}

$("show-log").addEventListener("click", () => {
  $("log-box").hidden = !$("log-box").hidden;
  $("show-log").textContent = $("log-box").hidden ? "Show log" : "Hide log";
});
$("copy-log").addEventListener("click", () => navigator.clipboard.writeText($("log").textContent).catch(() => {}));

// The flows report each check; the page only logs them (a failed one ends the flow with its message)
function check(id, label, ok, detail) {
  if (ok === null || ok === undefined) return;
  log(`check ${id}: ${ok === true ? "ok" : ok === false ? "FAILED" : "warning"} - ${label}${detail ? ` (${detail})` : ""}`);
}

// --- the step screen ---

let pending = null; // { resolve, reject } of the open question

// Text with the radio's key names shown as keys
function rich(text) {
  const out = document.createDocumentFragment();
  for (const part of String(text).split(/\b(SK1|PTT)\b/)) {
    if (part === "SK1" || part === "PTT") out.append(Object.assign(document.createElement("kbd"), { textContent: part }));
    else if (part) out.append(part);
  }
  return out;
}

function screen({ icon = "wait", title, steps = [], note = "", tone = "" }) {
  $("home").hidden = true;
  $("other").hidden = true;
  $("flow").hidden = false;
  $("icon").className = `icon ${tone}${icon === "wait" ? " spin" : ""}`;
  $("icon").innerHTML = svg(icon);
  $("title").textContent = title;
  const box = $("steps");
  if (steps.length === 1) {
    const p = document.createElement("p");
    p.append(rich(steps[0]));
    box.replaceChildren(p);
  } else {
    const ol = document.createElement("ol");
    for (const s of steps) {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.append(rich(s));
      li.append(span);
      ol.append(li);
    }
    box.replaceChildren(ol);
  }
  $("note").textContent = note;
  $("status").textContent = "";
  $("progress-row").hidden = true;
  $("saved-row").hidden = true;
  for (const b of ["primary", "secondary"]) $(b).hidden = true;
  $("flow").scrollIntoView({ block: "start", behavior: "smooth" });
}

function button(id, label, onClick) {
  const b = $(id);
  b.textContent = label;
  b.hidden = false;
  b.disabled = false;
  b.onclick = onClick;
}

function settle(how, value) {
  const p = pending;
  pending = null;
  $("primary").hidden = $("secondary").hidden = true;
  if (p) p[how](value);
}

const BAR = { check: "connect", backup: "backup", save: "backup", install: "install", verify: "install", done: "done" };

function bar(step) {
  const items = [...$("bar").children].filter((li) => !li.hidden);
  const at = items.findIndex((li) => li.dataset.step === step);
  items.forEach((li, i) => {
    li.classList.toggle("active", i === at);
    li.classList.toggle("done", i < at || step === "all");
  });
}

const ui = {
  phase(id) {
    bar(BAR[id] || id);
    log(`phase ${id}`);
  },
  check,
  status(text) {
    $("status").textContent = text || "";
    if (text) log(text);
  },
  progress(f, label) {
    $("progress-row").hidden = false;
    const p = $("progress");
    if (f === null) p.removeAttribute("value");
    else p.value = f;
    $("progress-label").textContent = f === null ? label : `${label} ${Math.floor(f * 100)}%`;
  },
  log,
  show({ id, icon, title, steps, note }) {
    screen({ icon: icon || "wait", title, steps, note });
    log(`${id || "show"}: ${title}`);
  },
  lockCancel(on) {
    $("cancel").hidden = on;
  },
  ask({ id, icon, title, steps, note, button: primary, secondary }) {
    screen({ icon, title, steps, note, tone: icon === "alert" ? "bad" : "" });
    log(`ask ${id || ""}: ${title}`);
    return new Promise((resolve, reject) => {
      pending = { resolve, reject };
      button("primary", primary, () => settle("resolve", "primary"));
      if (secondary) button("secondary", secondary, () => settle("resolve", "secondary"));
    });
  },
  saveBackup(bytes, name) {
    screen({
      icon: "save",
      title: "Save your backup",
      steps: ["Turn the radio off.", "Save the backup file somewhere safe."],
      note: "It’s your only way back to stock firmware.",
    });
    return new Promise((resolve, reject) => {
      pending = { resolve, reject };
      button("primary", "Save backup", async () => {
        try {
          if (await saveFile(bytes, name)) {
            log(`backup saved and read back (${name})`);
            settle("resolve");
          }
        } catch (e) {
          log(`save failed: ${e.message}`);
          $("status").textContent = `Couldn’t save the file (${e.message}). Try again.`;
        }
      });
    });
  },
  done({ id, title, steps }) {
    bar("all");
    screen({ icon: "check", title, steps, tone: "ok" });
    $("cancel").hidden = true;
    button("primary", "Done", home);
    log(`done ${id || ""}: ${title}`);
  },
};

// Save with the file picker and read the saved file back; without a picker, download and ask.
async function saveFile(bytes, name) {
  if (window.showSaveFilePicker) {
    let handle;
    try {
      handle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: "Radio backup", accept: { "application/octet-stream": [".bin"] } }] });
    } catch (e) {
      if (e.name === "AbortError") return false;
      throw e;
    }
    const w = await handle.createWritable();
    await w.write(bytes);
    await w.close();
    const back = new Uint8Array(await (await handle.getFile()).arrayBuffer());
    if (!equal(back, bytes)) throw new Error("it doesn’t read back the same");
    return true;
  }
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([bytes])), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  $("saved-row").hidden = false;
  $("saved-box").checked = false;
  $("primary").textContent = "Continue";
  $("primary").disabled = true;
  $("saved-box").onchange = () => ($("primary").disabled = !$("saved-box").checked);
  $("primary").onclick = () => settle("resolve");
  return false;
}

// --- running a flow ---

function onUnload(e) {
  e.preventDefault();
  e.returnValue = "";
}

function home() {
  $("flow").hidden = true;
  $("home").hidden = false;
  $("other").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function run(flow, { skip = [] } = {}) {
  if (running) return;
  // Ask for the port first, while the click still counts as a user gesture
  const portPromise = navigator.serial.requestPort();
  running = true;
  env.requestPort = () => portPromise;
  updateButtons();
  for (const li of $("bar").children) li.hidden = skip.includes(li.dataset.step);
  window.addEventListener("beforeunload", onUnload);
  let lock = null;
  try {
    lock = await navigator.wakeLock?.request("screen").catch(() => null);
  } catch {}
  try {
    await portPromise; // the person may close the picker: then nothing has happened
    bar("connect");
    $("cancel").hidden = false;
    await flow(env, ui);
  } catch (e) {
    fail(e);
  } finally {
    running = false;
    $("cancel").hidden = true;
    window.removeEventListener("beforeunload", onUnload);
    lock?.release?.().catch(() => {});
    env.link = null;
    updateButtons();
  }
}

function fail(e) {
  log(`stopped: ${e.name}: ${e.message}`);
  if (e.name === "NotFoundError") return home(); // no port picked
  if (e.stack && !(e instanceof CancelledError) && !e.code) log(e.stack);
  const cancelled = e instanceof CancelledError;
  const refused = e instanceof CheckError;
  screen({
    icon: cancelled ? "power" : "alert",
    tone: cancelled ? "" : "bad",
    title: cancelled ? "Cancelled" : refused ? "Can’t continue" : "Something went wrong",
    steps: cancelled ? ["Turn the radio off before starting again."] : [e.message, "Turn the radio off before trying again."],
  });
  if (!cancelled && !refused) $("note").textContent = "Show log (below) has the details if you need help.";
  button("primary", "Start over", home);
}

$("cancel").addEventListener("click", () => {
  log("cancel pressed");
  env.link?.cancel();
  settle("reject", new CancelledError());
});

$("start").addEventListener("click", () => run(install));
$("from-backup").addEventListener("click", () => run((env, ui) => installFromBackup(env, ui, backupBytes), { skip: ["backup"] }));
$("restore").addEventListener("click", () => run((env, ui) => restoreStock(env, ui, backupBytes), { skip: ["backup"] }));

$("backup-file").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  backupBytes = f ? new Uint8Array(await f.arrayBuffer()) : null;
  if (f) log(`backup file ${f.name}, ${f.size} bytes, sha256 ${(await sha256(backupBytes)).slice(0, 16)}…`);
  updateButtons();
});

function ready() {
  return Boolean(env.patch && env.reader && "serial" in navigator);
}

function updateButtons() {
  $("start").disabled = running || !ready();
  $("from-backup").disabled = $("restore").disabled = running || !ready() || !backupBytes;
}

// --- page-load checks ---

// The release this page was published from (index.html's kraken-release meta, written at deploy time):
// { tag, patch, reader } with the SHA-256 of kraken_patch.json and reader.bin, or null in a local build.
// The page uses only files with exactly those hashes, so it always installs the release it came from,
// even when a browser holds files of an older or newer deployment in its cache.
class PinError extends Error {
  constructor(file) {
    super(`${file} is not the one this page was published with`);
  }
}

function releasePin() {
  const raw = document.querySelector('meta[name="kraken-release"]')?.content || "";
  const pin = Object.fromEntries(raw.split(";").map((kv) => kv.trim().split("=")).filter((kv) => kv.length === 2));
  const ok = pin.tag && /^[0-9a-f]{64}$/.test(pin.patch || "") && /^[0-9a-f]{64}$/.test(pin.reader || "");
  $("release").textContent = ok ? `Kraken ${pin.tag}` : "Development build";
  if (ok) log(`release ${pin.tag}: patch ${pin.patch.slice(0, 12)}, reader ${pin.reader.slice(0, 12)}`);
  return ok ? pin : null;
}

function blocked(text) {
  $("blocked").textContent = text;
  $("blocked").hidden = false;
}

async function preflight() {
  const serialOk = "serial" in navigator && window.isSecureContext;
  check("browser", "Web Serial available", serialOk);
  if (!serialOk) blocked("This browser can’t connect to the radio. Open this page in Chrome or Edge on a computer.");

  try {
    const pin = releasePin();
    const r = await fetch("kraken_patch.json", { cache: "no-cache" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const raw = new Uint8Array(await r.arrayBuffer());
    if (pin && (await sha256(raw)) !== pin.patch) throw new PinError("kraken_patch.json");
    const patch = loadPatch(JSON.parse(new TextDecoder().decode(raw)));
    const rb = await fetch("reader.bin", { cache: "no-cache" });
    if (!rb.ok) throw new Error(`reader: HTTP ${rb.status}`);
    const bin = new Uint8Array(await rb.arrayBuffer());
    if (pin && (await sha256(bin)) !== pin.reader) throw new PinError("reader.bin");
    if (bin.length !== patch.reader.size || (await sha256(bin)) !== patch.reader.sha256) throw new Error("reader.bin does not match the patch file");
    env.reader = { bin, load: patch.reader.load, entry: patch.reader.entry };
    env.patch = patch;
    check("patch", `Kraken build ${patch.update.sha256.slice(0, 12)} loaded`, true);
  } catch (e) {
    check("patch", "Kraken build loaded", false, e.message);
    if (serialOk) blocked(e instanceof PinError ? "This page and its firmware files are from different releases. Reload the page." : "The installer didn’t load. Reload the page.");
  }
  updateButtons();
}

preflight();
