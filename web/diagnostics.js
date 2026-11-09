// The page's side of the diagnostics download (lib/diag.js has the recorder and the files): what the
// browser can tell about itself, the cables this page may use, and the screen right now. Then the zip.
// Safe to run at any moment, a flow running or not: it only reads.

import { zip } from "./lib/zip.js";

const $ = (id) => document.getElementById(id);
const tryOr = async (fn, fallback) => {
  try {
    return await fn();
  } catch (e) {
    return fallback === undefined ? { error: `${e.name}: ${e.message}` } : fallback;
  }
};

// The browser's events that explain many failures: a hidden tab (timers slow down), the cable coming and
// going, the computer going offline, errors nothing caught. Called once at page load.
export function watch(diag, log) {
  for (const level of ["log", "info", "warn", "error", "debug"]) {
    const orig = console[level].bind(console);
    console[level] = (...args) => {
      if (!(level === "log" && typeof args[0] === "string" && args[0].startsWith("[kraken] "))) diag.console(level, args);
      orig(...args);
    };
  }
  window.addEventListener("error", (e) => diag.error("error", e.error || { name: "Error", message: e.message, stack: `${e.filename}:${e.lineno}:${e.colno}` }));
  window.addEventListener("unhandledrejection", (e) => diag.error("unhandledrejection", e.reason));
  document.addEventListener("visibilitychange", () => log(`page ${document.visibilityState}`));
  window.addEventListener("focus", () => log("page focused"));
  window.addEventListener("blur", () => log("page lost focus"));
  document.addEventListener("freeze", () => log("page frozen by the browser"));
  document.addEventListener("resume", () => log("page resumed"));
  window.addEventListener("online", () => log("browser online"));
  window.addEventListener("offline", () => log("browser offline"));
  const id = (info = {}) => `${(info.usbVendorId ?? info.vendorId)?.toString(16).padStart(4, "0")}:${(info.usbProductId ?? info.productId)?.toString(16).padStart(4, "0")}`;
  navigator.serial?.addEventListener?.("connect", (e) => log(`serial port connected (${id(e.target?.getInfo?.())})`));
  navigator.serial?.addEventListener?.("disconnect", (e) => log(`serial port disconnected (${id(e.target?.getInfo?.())})`));
  navigator.usb?.addEventListener?.("connect", (e) => log(`USB device connected (${id(e.device)})`));
  navigator.usb?.addEventListener?.("disconnect", (e) => log(`USB device disconnected (${id(e.device)})`));
}

function osName(ua) {
  const v = ua?.platformVersion || "";
  const major = parseInt(v, 10);
  if (ua?.platform === "Windows") return `Windows ${major >= 13 ? "11" : major > 0 ? "10" : "(older than 10)"} (platform ${v})`;
  if (ua?.platform) return `${ua.platform} ${v}`.trim();
  return navigator.platform || "unknown";
}

async function browserInfo() {
  const uaData = navigator.userAgentData
    ? await tryOr(() => navigator.userAgentData.getHighEntropyValues(["architecture", "bitness", "model", "platformVersion", "fullVersionList", "wow64"]))
    : null;
  const brand = uaData?.fullVersionList?.filter((b) => !/not.?a.?brand/i.test(b.brand)).map((b) => `${b.brand} ${b.version}`).join(", ");
  return {
    summary: brand || navigator.userAgent,
    os: `${osName(uaData)}${uaData?.architecture ? `, ${uaData.architecture} ${uaData.bitness || ""}-bit` : ""}${uaData?.wow64 ? " (WoW64)" : ""}`,
    userAgent: navigator.userAgent,
    uaData,
    language: navigator.language,
    languages: navigator.languages,
    timezoneOffsetMin: new Date().getTimezoneOffset(),
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemoryGB: navigator.deviceMemory,
    maxTouchPoints: navigator.maxTouchPoints,
    screen: { width: screen.width, height: screen.height, pixelRatio: window.devicePixelRatio },
    viewport: { width: innerWidth, height: innerHeight },
    darkMode: matchMedia("(prefers-color-scheme: dark)").matches,
    battery: navigator.getBattery ? await tryOr(async () => {
      const b = await navigator.getBattery();
      return { charging: b.charging, level: b.level };
    }) : undefined,
  };
}

function features() {
  const policy = document.featurePolicy || document.permissionsPolicy;
  return {
    webSerial: "serial" in navigator,
    webUSB: "usb" in navigator,
    secureContext: window.isSecureContext,
    filePicker: "showSaveFilePicker" in window,
    wakeLock: "wakeLock" in navigator,
    compression: typeof CompressionStream === "function",
    ...(policy?.allowsFeature ? { serialAllowed: policy.allowsFeature("serial"), usbAllowed: policy.allowsFeature("usb") } : {}),
  };
}

// Ports and devices the person has already allowed this page to use (asking for more needs a click)
async function serialPorts() {
  if (!navigator.serial) return undefined;
  return tryOr(async () => (await navigator.serial.getPorts()).map((p) => ({
    ...p.getInfo(),
    connected: p.connected,
    open: Boolean(p.readable || p.writable),
  })));
}

async function usbDevices() {
  if (!navigator.usb) return undefined;
  const v = (a, b, c) => `${a}.${b}.${c}`;
  return tryOr(async () => (await navigator.usb.getDevices()).map((d) => ({
    vendorId: d.vendorId,
    productId: d.productId,
    manufacturerName: d.manufacturerName,
    productName: d.productName,
    hasSerialNumber: Boolean(d.serialNumber), // the number itself is left out
    deviceVersion: v(d.deviceVersionMajor, d.deviceVersionMinor, d.deviceVersionSubminor),
    usbVersion: v(d.usbVersionMajor, d.usbVersionMinor, d.usbVersionSubminor),
    deviceClass: d.deviceClass,
    deviceSubclass: d.deviceSubclass,
    deviceProtocol: d.deviceProtocol,
    opened: d.opened,
    configuration: d.configuration?.configurationValue,
    interfaces: d.configuration?.interfaces.map((i) => ({
      number: i.interfaceNumber,
      claimed: i.claimed,
      class: i.alternate?.interfaceClass,
      subclass: i.alternate?.interfaceSubclass,
      endpoints: i.alternate?.endpoints.map((e) => `${e.direction} ${e.type} ${e.endpointNumber} (${e.packetSize} B)`),
    })),
  })));
}

// What is on the page right now, as the person sees it
function screenNow() {
  const text = (id) => $(id)?.textContent?.trim() || undefined;
  const shown = (id) => $(id) && !$(id).hidden;
  return {
    view: shown("flow") ? "flow" : "home",
    title: shown("flow") ? text("title") : $("home")?.querySelector("h1")?.textContent,
    steps: shown("flow") ? [...$("steps").querySelectorAll("p, li")].map((e) => e.textContent.trim()) : undefined,
    note: text("note"),
    status: text("status"),
    progress: shown("progress-row") ? text("progress-label") : undefined,
    buttons: ["primary", "secondary", "cancel", "start", "from-backup", "restore"].filter((b) => shown(b)).map((b) => `${$(b).textContent.trim()}${$(b).disabled ? " (disabled)" : ""}`),
    blocked: shown("blocked") ? text("blocked") : undefined,
    bar: [...$("bar").children].filter((li) => !li.hidden).map((li) => `${li.textContent}${li.classList.contains("active") ? " (active)" : li.classList.contains("done") ? " (done)" : ""}`),
  };
}

export async function snapshot(page) {
  const [browser, ports, usb] = await Promise.all([browserInfo(), serialPorts(), usbDevices()]);
  return {
    page: {
      url: location.origin + location.pathname,
      ...page,
      visibility: document.visibilityState,
      focused: document.hasFocus(),
      online: navigator.onLine,
      crossOriginIsolated: window.crossOriginIsolated,
    },
    browser,
    features: features(),
    serialPorts: ports,
    usbDevices: usb,
    onScreen: screenNow(),
  };
}

function stamp(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}-${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`;
}

// Build the zip and hand it to the browser as a download. Returns the file name.
export async function saveDiagnostics(diag, page) {
  const now = new Date();
  const files = diag.files(await snapshot(page));
  const bytes = await zip(files, { date: now });
  const name = `kraken-diagnostics-${stamp(now)}.zip`;
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([bytes], { type: "application/zip" })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  return { name, size: bytes.length };
}
