#!/usr/bin/env node
// The web installer's diagnostics download, without a build: the ZIP writer (web/lib/zip.js), checked
// by Python's zipfile, and the recorder (web/lib/diag.js): what the trace keeps and what it never keeps.
//
//   node tests/web_diag_test.mjs
//
// tests/web_flasher_test.mjs runs the real flows with a recorder and checks no firmware reaches it.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { zip } from "../web/lib/zip.js";
import { Recorder } from "../web/lib/diag.js";
import { frame, readFrame, T } from "../web/lib/kr.js";

let failures = 0;
function ok(cond, what) {
  console.log(`  ${cond ? "PASS" : "FAIL"} ${what}`);
  if (!cond) failures++;
}

// Unpack with Python's zipfile (an independent reader): { name: base64 of contents }, after testzip()
function unzipWithPython(bytes) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kraken-diag-"));
  const file = path.join(dir, "d.zip");
  fs.writeFileSync(file, bytes);
  const py = [
    "import base64, json, sys, zipfile",
    "z = zipfile.ZipFile(sys.argv[1])",
    "assert z.testzip() is None",
    "print(json.dumps({i.filename: [i.compress_type, base64.b64encode(z.read(i)).decode()] for i in z.infolist()}))",
  ].join("\n");
  try {
    return JSON.parse(execFileSync("python3", ["-I", "-c", py, file], { encoding: "utf8" }));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

console.log("\nzip: deflated and stored files read back exactly");
{
  const text = "Kraken diagnostics\n".repeat(500) + "naïve UTF-8 ✓\n";
  const bin = Uint8Array.from({ length: 3000 }, (_, i) => (i * 131 + 7) & 0xff);
  const tiny = "x";
  for (const compress of [true, false]) {
    const z = await zip([{ name: "a.txt", data: text }, { name: "dir/b.bin", data: bin }, { name: "ü.txt", data: tiny }], { compress });
    const out = unzipWithPython(z);
    ok(Buffer.from(out["a.txt"][1], "base64").toString("utf8") === text, `text file intact (${compress ? "deflate" : "stored"})`);
    ok(Buffer.compare(Buffer.from(out["dir/b.bin"][1], "base64"), Buffer.from(bin)) === 0, `binary file intact (${compress ? "deflate" : "stored"})`);
    ok(Buffer.from(out["ü.txt"][1], "base64").toString("utf8") === tiny, "UTF-8 file name");
    if (compress) {
      ok(out["a.txt"][0] === 8 && z.length < text.length / 4, `repetitive text is deflated (${z.length} bytes)`);
      ok(out["ü.txt"][0] === 0, "a file deflate cannot shrink is stored");
    } else ok(Object.values(out).every(([m]) => m === 0), "compress: false stores every file");
  }
}

console.log("\nrecorder: received bytes kept only in raw mode");
{
  const d = new Recorder();
  d.rx(Uint8Array.from([0x02, 0x24, 0x00, 0x03]), 115200);
  d.mode("framed");
  const secret = Uint8Array.from({ length: 4096 }, (_, i) => (i * 29 + 3) & 0xff);
  d.rx(secret, 1500000);
  d.mode("raw");
  d.rx(Uint8Array.from([0x06]), 115200);
  const items = d.trace.items();
  const rx = items.filter((e) => e.k === "rx");
  ok(rx.length === 2 && rx[0].hex === "02240003" && rx[1].hex === "06", "boot ROM and updater bytes are kept");
  ok(!JSON.stringify(items).includes(Buffer.from(secret.subarray(0, 4)).toString("hex")) && d.counts.rxFramedBytes === 4096, "4 KB received while framed: counted, not kept");
  ok(d.trace.items().filter((e) => e.k === "ev" && /trace mode/.test(e.text)).length === 2, "mode changes are in the trace");
}

console.log("\nrecorder: labelled writes are kept as length and CRC only");
{
  const d = new Recorder();
  const block = Uint8Array.from({ length: 1028 }, (_, i) => (i * 7) & 0xff);
  d.tx(block, 115200, "block 1 of 2");
  d.tx(Uint8Array.from([0x52]), 115200);
  const [b, r] = d.trace.items();
  ok(b.what === "block 1 of 2" && b.n === 1028 && b.hex === undefined && typeof b.crc === "number", "firmware block: label, length, CRC");
  ok(r.hex === "52", "a short command is kept");
  ok(d.traceText().includes("[block 1 of 2] crc32"), "trace.txt shows the label");
}

console.log("\nrecorder: reader frames are headers only");
{
  const d = new Recorder();
  // a fake link that serves bytes and carries the trace, as Link does
  const wire = [
    frame(T.HELLO, Uint8Array.from([0x4b, 0x52, 0x44, 1, 13, 0, 0, 0])),
    Uint8Array.from([0x11, 0x22]),
    frame(T.DATA, Uint8Array.from([0x00, 0x10, 0, 0, ...Array.from({ length: 64 }, (_, i) => 0xa0 + (i & 15))])),
    frame(T.SUMSR, new Uint8Array(16)),
  ];
  let buf = Buffer.concat(wire.map((w) => Buffer.from(w)));
  const link = {
    trace: d,
    async read(n) {
      const out = buf.subarray(0, n);
      buf = buf.subarray(n);
      return new Uint8Array(out);
    },
  };
  for (let i = 0; i < 3; i++) await readFrame(link, 100);
  const fr = d.trace.items().filter((e) => e.k === "frame");
  ok(fr[0].type === T.HELLO && fr[0].hex === "4b524401" + "0d000000", "HELLO keeps its payload");
  ok(fr[1].type === T.DATA && fr[1].addr === 0x1000 && fr[1].hex === undefined && fr[1].noise === 2, "DATA keeps its address only, and the noise before it is counted");
  ok(fr[2].sums === 4 && fr[2].hex === undefined, "SUMS keeps the count only");
  ok(!d.traceText().includes("a0 a1 a2"), "no DATA bytes in trace.txt");
}

console.log("\nrecorder: coalescing and bounds");
{
  let now = 0;
  const d = new Recorder({ now: () => now, traceHead: 5, traceTail: 10 });
  for (const b of [0x02, 0x05, 0x00, 0x03]) d.rx(Uint8Array.from([b]), 115200); // one byte per USB packet
  ok(d.trace.length === 1 && d.trace.items()[0].hex === "02050003", "bytes arriving together form one event");
  now = 1000;
  d.rx(Uint8Array.from([0x43]), 115200);
  ok(d.trace.length === 2, "a byte a second later is a new event");
  for (let i = 0; i < 100; i++) d.event(`e${i}`);
  const items = d.trace.items();
  ok(items.length === 15 && d.trace.dropped === 87, `the trace keeps the first 5 and the last 10 (${items.length}, ${d.trace.dropped} dropped)`);
  ok(items[2].text === "e0" && items[14].text === "e99" && items[5].text === "e90", "head and tail in order");
  ok(d.traceText().includes("87 event(s) dropped here"), "trace.txt says where events were dropped");
}

console.log("\nrecorder: the files");
{
  const d = new Recorder();
  d.log("phase check");
  d.check("version", "Firmware is DM32.00.01.048", false, "The radio reports DM32.01.L01.048.");
  d.flowStart("install");
  d.note("cable", { usbVendorId: 0x067b, usbProductId: 0x2303, path: "Web Serial" });
  d.note("reader", { version: 1, divisor: 13, baud: 115200, tries: [] });
  d.add("flash", { what: "Kraken", result: "failed", code: "no-reply", message: "The radio stopped answering (block 3 of 951).", blocks: 951, acked: 2 });
  const err = Object.assign(new Error("The radio stopped answering."), { code: "no-reply" });
  d.flowEnd("failed", err);
  d.error("unhandledrejection", new TypeError("x is undefined"));
  d.console("warn", ["something", { a: 1 }]);
  const files = d.files({ page: { release: "v1.0", url: "https://example.invalid/" }, browser: { summary: "Chromium 142", os: "Linux" }, serialPorts: [{ usbVendorId: 0x067b, usbProductId: 0x2303, connected: true }] });
  const by = Object.fromEntries(files.map((f) => [f.name, f.data]));
  ok(["README.txt", "summary.txt", "diagnostics.json", "log.txt", "trace.txt", "console.txt"].every((n) => typeof by[n] === "string"), "six files");
  const sum = by["summary.txt"];
  ok(/067b:2303 \(Prolific PL2303\) via Web Serial/.test(sum), "summary names the cable");
  ok(/install .*failed .*\[no-reply\]/.test(sum), "summary has the flow and its error code");
  ok(/2\/951 blocks/.test(sum), "summary has how far the install got");
  ok(/version \(failed: The radio reports DM32.01.L01.048.\)/.test(sum), "summary lists the failed check");
  ok(/Chromium 142/.test(sum) && /v1\.0/.test(sum), "summary has the browser and the release");
  const json = JSON.parse(by["diagnostics.json"]);
  ok(json.format === "kraken-diagnostics-1" && json.flows[0].error.code === "no-reply" && json.facts.flash[0].acked === 2, "diagnostics.json has the facts");
  ok(/phase check/.test(by["log.txt"]) && /x is undefined/.test(by["console.txt"]) && /something \{"a":1\}/.test(by["console.txt"]), "log and console");
  const z = await zip(files);
  ok(Object.keys(unzipWithPython(z)).length === 6, `zips and unzips (${z.length} bytes)`);
}

console.log(failures ? `\n${failures} FAILED` : "\nALL PASS");
process.exit(failures ? 1 : 0);
