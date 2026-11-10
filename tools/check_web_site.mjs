#!/usr/bin/env node
// Check an assembled web-installer site before it is published: every file the page loads is there, the
// patch file parses with the page's own loader, and reader.bin is the one the patch file names.
//
//   node tools/check_web_site.mjs out/kraken/web             (after tools/build.sh; a development build)
//   node tools/check_web_site.mjs _site --release            (what .github/workflows/pages.yml deploys:
//                                                             it must be pinned to its release)

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const release = args.includes("--release");
const dir = path.resolve(args.find((a) => !a.startsWith("--")) || "out/kraken/web");
let fails = 0;
const ok = (cond, what) => {
  console.log(`  ${cond ? "PASS" : "FAIL"} ${what}`);
  if (!cond) fails++;
};

const need = ["index.html", "app.js", "style.css", "favicon.svg", "kraken_patch.json", "reader.bin"];
for (const f of need) ok(fs.existsSync(path.join(dir, f)), `${f} present`);

// every module the page imports, followed from app.js
const seen = new Set();
function follow(rel) {
  const file = path.join(dir, rel);
  if (seen.has(file)) return;
  seen.add(file);
  if (!fs.existsSync(file)) return ok(false, `${rel} present (imported)`);
  const src = fs.readFileSync(file, "utf8");
  for (const m of src.matchAll(/^\s*import\s[^"']*["'](\.[^"']+)["']/gm)) follow(path.join(path.dirname(rel), m[1]));
}
follow("app.js");
ok(seen.size > 1, `${seen.size} page modules found and present`);

const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
ok(!/(src|href)="\//.test(html), "index.html uses relative paths only (the site is served under /<repo>/)");

if (fs.existsSync(path.join(dir, "kraken_patch.json")) && fs.existsSync(path.join(dir, "reader.bin"))) {
  const { loadPatch } = await import(pathToFileURL(path.join(dir, "lib", "image.js")).href);
  let patch = null;
  try {
    patch = loadPatch(JSON.parse(fs.readFileSync(path.join(dir, "kraken_patch.json"), "utf8")));
    ok(true, `kraken_patch.json loads (${patch.builds.map((b) => `${b.firmware} update ${b.update.sha256.slice(0, 12)}`).join(", ")})`);
  } catch (e) {
    ok(false, `kraken_patch.json loads (${e.message})`);
  }
  if (patch) {
    const bin = fs.readFileSync(path.join(dir, "reader.bin"));
    const sha = crypto.createHash("sha256").update(bin).digest("hex");
    ok(bin.length === patch.reader.size && sha === patch.reader.sha256, `reader.bin is the one the patch names (${bin.length} bytes, ${sha.slice(0, 12)})`);
  }
}

// the release pin (index.html's kraken-release meta, written by tools/pin_web_release.py)
const metas = [...html.matchAll(/<meta name="kraken-release" content="([^"]*)">/g)];
ok(metas.length === 1, "index.html has one kraken-release meta");
const pin = Object.fromEntries((metas[0]?.[1] || "").split(";").map((kv) => kv.split("=")).filter((kv) => kv.length === 2));
if (pin.tag) {
  const file = (f) => (fs.existsSync(path.join(dir, f)) ? crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, f))).digest("hex") : "missing");
  ok(pin.patch === file("kraken_patch.json") && pin.reader === file("reader.bin"), `pinned to ${pin.tag}, and both files match the pin`);
} else {
  ok(!release, release ? "pinned to a release (run tools/pin_web_release.py)" : "development build (not pinned)");
}

console.log(fails ? `${fails} FAILED` : "ALL PASS");
process.exit(fails ? 1 : 0);
