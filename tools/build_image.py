#!/usr/bin/env python3
"""Build the combined DM-32UV Kraken image from image.json, then stage it for flashing.

Runs INSIDE the dm32uv-re Docker image (see tools/build.sh and docs/building.md), with this repo mounted at
/w/dm32uv-unleashed and the dm32uv-re checkout at /w/dm32/dm32uv-re. image.json's paths are relative to /w.

Steps:
  0. tools/gen_menu_hosts.py, gen_manifest.py and gen_claims.py with --check: the stock-menu host rows and
     generator-call sites, the branch hooks and the blob list in kraken_ui.patch.json, krk_menu_rows.h and
     krk_claims.h must match menu_hosts.json, hooks.json, link.json and allocations.json. Before the kraken_ui step,
     tools/pack_kraken.py places Kraken (one link) in the free space and writes the builder's blobs and claims.
  1. Build each manifest in image.json's order with dm32uv-re's build_manifest_patch.py. Patch N
     uses patch N-1's output image as its --base, so every builder safety check (expected stock
     bytes, erased landing zones, claim overlaps, "every changed byte is declared") runs against
     the image the patch is actually applied to.
  2. Copy the last image to out/kraken/ and check that the sectors changed versus stock are
     exactly the union of the sectors each step changed (no step touched another step's sector).
  3. Stage the changed sectors with stage_sectors.py (refuses sensitive and QEMU-only sectors)
     and write a BPS patch with bps_patch.py and the universal BFUV32-V2 update file with
     make_official_update.py (the file users flash; docs/flashing.md). If DM32_OFFICIAL_SAMPLE names an
     official Baofeng update file, its header is compared with ours; otherwise that check is skipped.
     Then the web installer's site in out/kraken/web/: web/, our reader program (reader/, built by
     build_reader.py) and kraken_patch.json from make_web_patch.py (docs/web-flasher.md).
  4. Unless --no-test, run each patch's own QEMU test against the COMBINED image, by loading the
     test module and pointing its PATCHED/ELF globals at the combined image and this build's ELF.

All of that is done once per stock firmware build in firmware/firmwares.json (docs/firmware-builds.md), one after
the other: the reference build (DM32.00.01.048) from this repo's own files into out/kraken/, every other build from
the copies tools/fw_translate.py writes under out/fw/<id>/ (sources, manifests and tests with each stock address
translated through firmware/<id>/addrmap.json) into out/kraken-<id>/. Every builder check runs against that build's
own stock image; dm32uv-re's tools get that build's symbols, policy files and QEMU-only patch sites through
tools/re_tool.py. Step 0 also checks firmware/<id>/addrmap.json with tools/gen_fw_maps.py --check. At the end
out/kraken/release/ (what a release carries: kraken_patch.json, the web installer's patch with one entry per build,
and kraken_reader.bin; no stock firmware) and out/kraken/updates/ (one update file per build, named after its
firmware, for installing by hand; never published: it contains the stock firmware) are written.

  build_image.py [--fw ID ...] [--no-test]     --fw: only these builds (default all)

Nothing here flashes a radio. Flashing is the manual procedure in docs/flashing.md.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

W = pathlib.Path("/w")
HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
RE = W / "dm32/dm32uv-re"
RE_TOOLS = RE / "tools/src/dm32_patch_tools"
SECTOR = 0x1000
FLASH_BASE = 0x03000000     # sector numbers are offsets into the 1 MB image; the flash is mapped here
sys.path.insert(0, str(HERE))
import fwreg  # noqa: E402


def fw_dir(fw):
    """Where tools/fw_translate.py writes a non-reference build's sources, manifests, tests and dm32uv-re files."""
    return REPO / "out" / "fw" / fw["id"]


def fw_out(fw, base):
    """The build's output folder: out/kraken/ for the reference, out/kraken-<id>/ for the others."""
    return base if fwreg.is_reference(fw) else base.with_name(f"{base.name}-{fw['id']}")


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def changed_sectors(a, b):
    return {i for i in range(0, len(a), SECTOR) if a[i:i + SECTOR] != b[i:i + SECTOR]}


def run(cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def build(cfg, out, fw):
    """One stock firmware build: fw is its firmware/firmwares.json entry, cfg its image.json (the repo's, or the copy
    tools/fw_translate.py wrote), out its output folder."""
    ref = fwreg.is_reference(fw)
    stock_path = W / cfg["base_image"]
    stock = stock_path.read_bytes()
    # image.json "exclude_claims": claims of another repo that this image provably never uses (each
    # entry names the file and says why); the file is passed on as a filtered copy without them
    excl = {(e["file"], e["name"]) for e in cfg.get("exclude_claims", [])}

    def filter_claims(file, drop, out_name):
        """A copy of the allocations file without the claims named in drop, written to out/<out_name>."""
        src = json.loads((W / file).read_text())
        missing = drop - {c["name"] for c in src["allocations"]}
        if missing:
            sys.exit(f"exclude_claims: {sorted(missing)} not in {file}")
        src["allocations"] = [c for c in src["allocations"] if c["name"] not in drop]
        filt = out / out_name
        filt.write_text(json.dumps(src, indent=1) + "\n")
        print(f"allocations: {file} without {sorted(drop)} -> {filt}")
        return filt

    extra = []
    for f in cfg["allocation_files"]:
        drop = {n for (ef, n) in excl if ef == f}
        extra += ["--extra-allocations", filter_claims(f, drop, f"allocations_filtered_{len(extra)}.json") if drop else W / f]
    # the builder's own registry (always loaded, not an --extra-allocations file): filtered the same way and handed to
    # the builder through tools/re_tool.py; for another firmware build, fw_translate's copy of it (forbidden ranges moved)
    own = "dm32/dm32uv-re/data/allocations.json"
    own_src = own if ref else str(fw_dir(fw) / "re" / "allocations.json")
    drop = {n for (ef, n) in excl if ef == own}
    registry = filter_claims(own_src, drop, "allocations_filtered_builder.json") if drop else W / own_src
    builder = [sys.executable, HERE / "re_tool.py", "--fw", fw["id"],
               "--set", f"build_manifest_patch.ALLOCATIONS_JSON={registry}", "build_manifest_patch"]

    # gas silently drops `bsr <absolute>`: refuse to build if any patch source does that
    run([sys.executable, HERE / "check_bsr.py", (REPO if ref else fw_dir(fw)) / "patches"])

    base, steps, owner = stock_path, [], {}
    for i, p in enumerate(cfg["patches"]):
        manifest = W / p["manifest"]
        mj = json.loads(manifest.read_text())
        name = mj["name"]
        step_out = out / "steps" / f"{i:02d}_{name}"
        if step_out.exists():
            shutil.rmtree(step_out)
        step_extra = list(extra)
        pack = None
        if "pack" in mj:
            # one link, placed in this build's free space: pack_kraken.py writes the builder's manifest (its blobs) and the
            # claims for exactly what it used (docs/flash-allocation.md, "How Kraken is placed")
            pack = out / "pack" / name
            run([sys.executable, HERE / "pack_kraken.py", "--fw", fw["id"], "plan", manifest, "--base", base,
                 "--registry", registry, *extra, "--out", pack])
            manifest = pack / manifest.name
            step_extra += ["--extra-allocations", pack / "claims.json"]
        run([*builder, manifest, "--base", base,
             *step_extra, "--require-claims", "--out", step_out])
        summary = json.loads((step_out / "summary.json").read_text())
        img_path = step_out / summary["image"]["file"]
        if pack:
            # the built blobs must be the master link, byte for byte; copies kraken_ui.elf/.map into build/ for the tests
            run([sys.executable, HERE / "pack_kraken.py", "verify", "--out", pack, "--image", img_path,
                 "--build", step_out / "build"])
        prev = base.read_bytes()
        img = img_path.read_bytes()
        mine = changed_sectors(prev, img)
        # Two patches may share a 4 KiB sector (each builder run checks its own expected bytes
        # against the previous image, so they cannot overwrite each other); the sector is then
        # owned by both, and its staged file carries both patches.
        for s in mine:
            owner[s] = f"{owner[s]}+{name}" if s in owner else name
        # the next step's builder requires a .sha256 sidecar on its base
        img_path.with_name(img_path.name + ".sha256").write_text(f"{sha256(img)}  {img_path.name}\n")
        steps.append(dict(id=p["id"], name=name, out=step_out, image=img_path, summary=summary, test=p.get("test")))
        base = img_path

    final = out / f"progmem_{cfg['name'].upper()}_patched.bin"
    shutil.copyfile(base, final)
    img = final.read_bytes()
    total = changed_sectors(stock, img)
    if total != set(owner):
        sys.exit(f"sector bookkeeping mismatch: image changes {sorted(total)} but steps account for {sorted(owner)}")
    final.with_name(final.name + ".sha256").write_text(f"{sha256(img)}  {final.name}\n")

    staged = out / "staged"
    if staged.exists():
        shutil.rmtree(staged)
    expect = ",".join(f"{FLASH_BASE + s:#010x}" for s in sorted(total))
    run([sys.executable, HERE / "re_tool.py", "--fw", fw["id"], "stage_sectors", "--patched", final, "--stock", stock_path,
         "--out", staged, "--expect-sectors", expect])
    # rollback files: each changed sector's stock bytes, from the stock image itself
    rb = staged / "rollback"
    rb.mkdir()
    lines = []
    for s in sorted(total):
        f = rb / f"sector_{FLASH_BASE + s:08x}_stock.bin"
        f.write_bytes(stock[s:s + SECTOR])
        lines.append(f"{sha256(stock[s:s + SECTOR])}  {f.name}\n")
    (rb / "MANIFEST.sha256").write_text("".join(lines))
    bps = out / f"{cfg['name']}.bps"
    bps.unlink(missing_ok=True)
    run([sys.executable, RE_TOOLS / "bps_patch.py", "create", stock_path, final, bps])
    # the universal update file for the radio's own SK1+PTT updater (docs/flashing.md, "Universal update file")
    maps = sorted(m for s in steps for m in (s["out"] / "build").glob("*.map"))
    sample = os.environ.get("DM32_OFFICIAL_SAMPLE")
    if sample and not pathlib.Path(sample).is_file():
        sys.exit(f"DM32_OFFICIAL_SAMPLE={sample}: no such file")
    if not sample:
        print("note: DM32_OFFICIAL_SAMPLE not set; the update header is not compared with an official file", flush=True)
    update = out / f"{cfg['name']}_update_BFUV32-V2.bin"
    run([sys.executable, HERE / "make_official_update.py", "build", final, "--base", stock_path, "--firmware", fw["id"],
         "--out", update, "--maps", *maps, *(["--official-sample", sample] if sample else [])])

    # this build's entry of the web installer's patch: Kraken's bytes only, applied in the browser to the stock
    # firmware read off the radio (finish() puts every build's entry in one kraken_patch.json; .github/workflows/
    # pages.yml publishes the page with it, docs/web-flasher.md "Publishing")
    run([sys.executable, HERE / "make_web_patch.py", "entry", "--firmware", fw["id"], "--stock", stock_path,
         "--image", final, "--update", update, "--out", out / "web_entry.json"])

    report = dict(
        name=cfg["name"], firmware=fw["version"], base=dict(path=str(stock_path), sha256=sha256(stock)),
        image=dict(path=str(final), sha256=sha256(img)),
        sectors={f"{FLASH_BASE + s:#010x}": owner[s] for s in sorted(total)},
        patches=[dict(id=s["id"], name=s["name"], warnings=s["summary"].get("warnings", [])) for s in steps])
    (out / "build.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"\nIMAGE  {final}\nsha256 {sha256(img)}\nsectors changed: {len(total)}")
    for a, o in report["sectors"].items():
        print(f"  {a}  {o}")
    return final, steps


def run_one(spec_path):
    """Child mode (--run-one): run a single patch's test in its own process, against the QEMU gdb
    port in DM32_GDB_PORT. Same loading, ELF-override and retry semantics the in-process runner had."""
    spec = json.loads(pathlib.Path(spec_path).read_text())
    script = pathlib.Path(spec["script"])
    sys.path.insert(0, str(script.parent))
    s = importlib.util.spec_from_file_location(script.stem, script)
    mod = importlib.util.module_from_spec(s)
    sys.modules[script.stem] = mod
    s.loader.exec_module(mod)
    setattr(mod, spec["var"], pathlib.Path(spec["patched"]))
    if hasattr(mod, "ELF"):
        # the test reads symbols from its blob's ELF; use this build's copy, same file name
        elf = pathlib.Path(spec["out"]) / "build" / pathlib.Path(mod.ELF).name
        if not elf.exists():
            sys.exit(f"{elf} missing: cannot point {script.name} at this build's ELF")
        mod.ELF = elf
    rc = 1
    for attempt in range(1 + spec.get("retries", 0)):
        sys.argv = [str(script), *spec.get("args", [])]
        for m_ in (mod, sys.modules.get("mi_rng_test")):
            if m_ is not None and hasattr(m_, "ok"):
                m_.ok = True                        # the test module's sticky failure flag
        try:
            mod.main()
            rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except Exception as e:                      # e.g. a gdb socket timeout in a booted phase
            print(f"{spec['id']}: {type(e).__name__}: {e}", flush=True)
            rc = 1
        if rc == 0:
            break
        if attempt < spec.get("retries", 0):
            print(f"===== {spec['id']}: attempt {attempt + 1} failed, retrying (declared flaky in image.json) =====", flush=True)
    sys.exit(rc)


def test_env(port, out, fw):
    """A test child's environment: its gdb port and build folder; for a non-reference build also DM32_FW (the tests
    then compose QEMU images for that build) and DM32_WS (they run from out/fw/<id>/tests/, not the repo)."""
    env = dict(os.environ, DM32_GDB_PORT=port, DM32_KRAKEN_OUT=str(out))
    env.pop("DM32_FW", None)
    if not fwreg.is_reference(fw):
        env.update(DM32_FW=fw["id"], DM32_WS=str(W))
    return env


def run_tests(final, steps, out, fw):
    """Run every patch's test concurrently, each in a --run-one child with its own QEMU gdb port
    (f13's second machine hard-codes 1235, so the assigned ports stay clear of 1234/1235).
    Each child's output goes to a log in `out` and is printed whole when it finishes."""
    jobs = []
    for i, s in enumerate(steps):
        t = s["test"]
        if not t:
            continue
        spec = dict(id=s["id"], script=str(W / t["script"]), patched=str(final),
                    var=t["patched_image_var"], args=t.get("args", []),
                    retries=t.get("retries", 0), out=str(s["out"]))
        sp = out / f"test_{i:02d}.spec.json"
        sp.write_text(json.dumps(spec, indent=1) + "\n")
        log = out / f"test_{i:02d}.log"
        port = str(1240 + 10 * i)
        p = subprocess.Popen([sys.executable, __file__, "--run-one", str(sp)],
                             stdout=open(log, "w"), stderr=subprocess.STDOUT,
                             env=test_env(port, out, fw))
        jobs.append((s, p, log, time.time()))
        print(f"===== [{fw['version']}] test {s['id']} {s['name']}: {pathlib.Path(t['script']).name} against the combined image "
              f"(parallel, gdb port {port}, {log.name}) =====", flush=True)
    results = {}
    for s, p, log, t0 in jobs:
        rc = p.wait()
        results[s["id"]] = rc
        print(f"\n===== {s['id']} done, exit {rc}, {time.time() - t0:.1f} s wall — {log.name} =====")
        print(log.read_text(), flush=True)
    print(f"\n===== test summary ({fw['version']}) =====")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v == 0 else f'FAIL (exit {v})'}")
    return all(v == 0 for v in results.values())


def finish(built, base_out):
    """release/: the files a release carries, kraken_patch.json (every build's web entry, docs/web-flasher.md) and
    kraken_reader.bin. updates/: kraken_<firmware>_update_BFUV32-V2.bin per build (docs/flashing.md), for installing by
    hand; they contain the stock firmware, so they are never published. Plus the web site out/kraken/web/."""
    site = base_out / "web"
    run([sys.executable, HERE / "build_reader.py", "--out", base_out / "reader"])
    run([sys.executable, HERE / "make_web_patch.py", "site", "--reader", base_out / "reader" / "reader.json",
         "--site", site, "--out", site / "kraken_patch.json", "--name", "kraken",
         *[o / "web_entry.json" for _fw, o in built]])
    for src, name in ((site / "kraken_patch.json", "kraken_patch.json"), (site / "reader.bin", "kraken_reader.bin")):
        data = src.read_bytes()
        (base_out / name).write_bytes(data)
        (base_out / f"{name}.sha256").write_text(f"{sha256(data)}  {name}\n")
    rel, upd = base_out / "release", base_out / "updates"
    for d in (rel, upd):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
    for fw, o in built:
        name = f"kraken_{fw['version']}_update_BFUV32-V2.bin"
        data = (o / "kraken_update_BFUV32-V2.bin").read_bytes()
        (upd / name).write_bytes(data)
        (upd / f"{name}.sha256").write_text(f"{sha256(data)}  {name}\n")
    for name in ("kraken_patch.json", "kraken_reader.bin"):
        shutil.copy2(base_out / name, rel / name)
        shutil.copy2(base_out / f"{name}.sha256", rel / f"{name}.sha256")
    for d, what in ((rel, "RELEASE (publish these)"), (upd, "UPDATE FILES (contain stock firmware: never publish)")):
        print(f"\n{what} {d}")
        for f in sorted(d.iterdir()):
            if not f.name.endswith(".sha256"):
                print(f"  {f.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=pathlib.Path, default=REPO / "image.json",
                    help="another image.json (a measurement variant): built for the reference firmware only")
    ap.add_argument("--out", type=pathlib.Path, default=REPO / "out" / "kraken")
    ap.add_argument("--fw", action="append", help="build only this firmware (id or version; repeatable)")
    ap.add_argument("--no-test", action="store_true")
    ap.add_argument("--run-one", type=pathlib.Path, help="internal: run one test spec in this process")
    args = ap.parse_args()
    if args.run_one:
        run_one(args.run_one)
        return
    variant = args.config.resolve() != (REPO / "image.json").resolve()
    fws = [fwreg.reference()] if variant else fwreg.firmwares()
    if args.fw:
        want = {fwreg.get(k)["id"] for k in args.fw}
        fws = [f for f in fws if f["id"] in want]
    # the manifest's host rows / hooks / blobs and the C headers are generated: refuse to build from a stale one
    for gen in ("gen_menu_hosts.py", "gen_manifest.py", "gen_claims.py"):
        run([sys.executable, HERE / gen, "--check"])
    # every Kraken source goes through the pack: no hand-made blob, linker script or unpinned claim (docs/flash-allocation.md)
    run([sys.executable, HERE / "pack_kraken.py", "lint"])
    others = [f["id"] for f in fws if not fwreg.is_reference(f)]
    if others:
        # every stock address of every non-reference build must have its checked translation
        run([sys.executable, HERE / "gen_fw_maps.py", "--check", *others])
        run([sys.executable, HERE / "fw_translate.py", *others])
    built, failed = [], []
    for fw in fws:
        print(f"\n########## {fw['version']} ##########", flush=True)
        out = fw_out(fw, args.out)
        out.mkdir(parents=True, exist_ok=True)
        cfg = json.loads((args.config if fwreg.is_reference(fw) else fw_dir(fw) / "image.json").read_text())
        final, steps = build(cfg, out, fw)
        built.append((fw, out))
        if not args.no_test and not run_tests(final, steps, out, fw):
            failed.append(fw["version"])
    if not variant:
        finish(built, args.out)
    if failed:
        sys.exit(f"tests failed on: {', '.join(failed)}")


if __name__ == "__main__":
    main()
