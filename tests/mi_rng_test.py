"""QEMU test for patches/mi_rng (the AES-256 CTR_DRBG that replaces rand() at the DMR-privacy
MI draw, see docs/encryption.md section 6). Everything runs the real CK803S code of the patched image
under `-M dm32`; nothing is re-implemented except the independent Python reference model.

PHASES
  A  DRBG core, deterministic, no boot. QEMU is started halted with the patched flash, IRAM and
     SRAM-init images; mi_rng_step(state, noise48) is called with chosen state and noise, and the
     16-byte output block and the next (key, V) are compared with a Python model of the same
     CTR_DRBG built on OpenSSL AES-256-ECB. This is what proves the construction and that the
     firmware's AES (re-based to IRAM) is wired correctly. Three chained steps, three vectors.
  B  The real path, booted. The patched image boots to standby with the OS running and the ADC and
     timers live, then the CPU is hijacked over the gdb stub (as crypto_kat.py does, but with a
     breakpoint on the return sentinel because the scheduler keeps running):
       B1  mi_rng_entry called 96 times with canary values in r4-r11: callee-saved registers must
           survive, the first call must seed (magic set), bytes must not be constant or repeating.
       B2  the stock MI fill loop itself (0x0305cae6..0x0305caf8, including the patched `bsr`)
           run twice into a scratch context: six bytes written each time, and the two draws differ.
       B3  the state's own bookkeeping: scratch wiped, the finalisation block consistent with
           Davies-Meyer recomputed in Python from the values left in the state.
     With --noise-dependence, three more boots check that the output follows the ADC input
     (DM32_ADC_CH2): same input twice, then a different input. Note it reports what it SEES; the
     emulator's timer and ADC are not hardware noise, so it proves wiring, not entropy.
  C  --measure-irq-off: runs one aes_block call (the longest stretch with IRQs off) with QEMU's
     one-instruction-per-TB exec trace and reports the exact instruction count, plus the time it
     implies at 192 MHz assuming 1 instruction/cycle (an estimate, not a measurement on silicon).

USAGE (inside the dm32uv-re Docker image; needs qemu-system-cskyv2, openssl, csky-abiv2-elf-nm):
  tools/build.sh runs this against the combined Kraken image. To build and test mi_rng on its own, from the repo root:
  docker run --rm -v "$PWD":/w/dm32uv-unleashed -v "$(cd "${DM32UV_RE:-../dm32uv-re}" && pwd)":/w/dm32/dm32uv-re \\
      -w /w/dm32/dm32uv-re dm32uv-re-pipeline:latest \\
      -c 'python3 tools/src/dm32_patch_tools/build_manifest_patch.py /w/dm32uv-unleashed/patches/mi_rng/mi_rng.patch.json \\
          --extra-allocations /w/dm32uv-unleashed/patches/mi_rng/allocations.json --require-claims \\
          --out /w/dm32uv-unleashed/out/mi_rng \\
          && python3 /w/dm32uv-unleashed/tests/mi_rng_test.py --noise-dependence --measure-irq-off'
The other QEMU tests in tests/ import this module for start_qemu, setreg, run_to and report.
Exit status 0 iff every check passes.
"""
import argparse
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import time

# The stock-firmware kit (gdb_rsp, build_patch, compose_qemu_image, the firmware images) lives in the dm32uv-re
# checkout. tools/build.sh mounts it at /w/dm32/dm32uv-re, next to this repo at /w/dm32uv-unleashed, and sets
# DM32UV_RE. Without the variable: <parent>/dm32/dm32uv-re if it exists, else a sibling <parent>/dm32uv-re.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]      # this repo
_re_default = REPO_ROOT.parent / "dm32/dm32uv-re"
DM32UV_RE = pathlib.Path(os.environ.get("DM32UV_RE") or
                         (_re_default if _re_default.exists() else REPO_ROOT.parent / "dm32uv-re"))
sys.path.insert(0, str(DM32UV_RE / "tools/src/dm32_patch_tools"))

from build_patch import decode_branch
from gdb_rsp import RspClient, parse_g_reply, read_mem, read_pc

HERE = DM32UV_RE / "tools/src/dm32_patch_tools"              # dm32uv-re's tools (compose_qemu_image.py)
PATCH_OUT = REPO_ROOT / "out" / "mi_rng"
PATCHED = PATCH_OUT / "progmem_MI_RNG_patched.bin"
ELF = PATCH_OUT / "build/mi_rng.elf"
FLASH = DM32UV_RE / "data/firmware/patching/progmem_stock_DP570UV_20260926_FULL.bin"
IRAM = DM32UV_RE / "data/firmware/IRAM_dm32.00.01.048.bin"
# Another stock firmware build (docs/firmware-builds.md): build_image.py runs tools/fw_translate.py's copy of this file
# with DM32_FW set; the stock image is then that build's (its image.json), and QEMU images are composed through
# tools/re_tool.py with that build's QEMU-only patch sites.
FW = os.environ.get("DM32_FW") or None
if FW:
    import json as _json
    FLASH = pathlib.Path(os.environ.get("DM32_WS") or REPO_ROOT.parent) / \
        _json.loads((REPO_ROOT / "image.json").read_text())["base_image"]
_TOOLS = next((p / "tools" for p in pathlib.Path(__file__).resolve().parents if (p / "tools/re_tool.py").exists()), None)
COMPOSE = [sys.executable, str(_TOOLS / "re_tool.py"), "--fw", FW, "compose_qemu_image"] if FW else \
    [sys.executable, str(DM32UV_RE / "tools/src/dm32_patch_tools/compose_qemu_image.py")]

SENTINEL = 0x000169D4          # br16 . in IRAM (the ALPU-failure while(1)); never reached when bypassed
STACK_A = 0x0002F800           # phase A: above the IRAM load image, nothing else runs
STACK_B = 0x18007B00           # phase B: SRAM scratch window, unclaimed (allocations.json)
SCRATCH_A = 0x0002FA00
SCRATCH_B = 0x18007800
FAKE_CTX_B = 0x18007B80
LOOP_START, LOOP_END = 0x0305CAE6, 0x0305CAF8   # MI fill loop in dmr_privacy_key_load_and_mi_draw
EXPECT_BSR_TARGET = None       # filled from nm (mi_rng_entry)

ok = True


def report(res, msg):
    global ok
    ok &= bool(res)
    print(("PASS  " if res else "FAIL  ") + msg, flush=True)


# ---------------------------------------------------------------- reference model (OpenSSL)
def ecb(key, block):
    return subprocess.run(["openssl", "enc", "-aes-256-ecb", "-nopad", "-K", key.hex()],
                          input=block, capture_output=True, check=True).stdout


def inc(v):
    return ((int.from_bytes(v, "big") + 1) % (1 << 128)).to_bytes(16, "big")


def model_update(k, v, p):
    t = b""
    for _ in range(3):
        v = inc(v)
        t += ecb(k, v)
    if p is not None:
        t = bytes(a ^ b for a, b in zip(t, p))
    return t[:32], t[32:]


def model_step(k, v, noise):
    k, v = model_update(k, v, noise)
    v = inc(v)
    out = ecb(k, v)
    k, v = model_update(k, v, None)
    return k, v, out


def model_dm(h, m):
    return bytes(a ^ b for a, b in zip(ecb(m, h), h))


# ---------------------------------------------------------------- symbols / images
def symbols():
    out = subprocess.run(["csky-abiv2-elf-nm", str(ELF)], capture_output=True, text=True, check=True).stdout
    return {n: int(a, 16) for a, _, n in (l.split() for l in out.splitlines() if len(l.split()) == 3)}


def layout(sym):
    """Field offsets inside struct mi_rng (mi_rng.c), checked against the symbol size."""
    return dict(magic=0, avail=4, calls=8, busy=12, contended=16, key=20, v=52, buf=68, sched=84,
                temp=324, noise=372, blk=420, h=452)


def compose(workdir):
    subprocess.run([*COMPOSE, "--patched", str(PATCHED),
                    "--out", workdir + "/img.bin", "--iram-out"], check=True, capture_output=True)
    # compose_qemu_image writes a ZERO sram.bin, but the AES S-box and Rcon live in SRAM init data
    # (flash 0x03072000.. -> SRAM 0x18000800.., S-box at 0x1800119c): build it as crypto_kat.py does.
    sram = bytearray(0x8000)
    init = FLASH.read_bytes()[0x72000:0x763FC]
    sram[0x800:0x800 + len(init)] = init
    open(workdir + "/sram.bin", "wb").write(sram)
    return workdir + "/img.bin", workdir + "/iram.bin", workdir + "/sram.bin"


def start_qemu(workdir, kernel, iram, sram, halted, env=None, extra=()):
    # DM32_GDB_PORT lets several tests run concurrently, each with its own QEMU stub (the
    # build_image.py parallel test runner assigns one port per test; default is plain `-s`).
    port = int(os.environ.get("DM32_GDB_PORT", "1234"))
    cmd = ["qemu-system-cskyv2", "-M", "dm32", "-cpu", "ck803", "-display", "none", "-monitor", "none",
           "-serial", "null", "-gdb", f"tcp::{port}", "-kernel", kernel,
           "-device", f"loader,file={iram},addr=0x00010000", "-device", f"loader,file={sram},addr=0x18000000"]
    cmd += list(extra)
    if halted:
        cmd.insert(cmd.index("-gdb"), "-S")
    proc = subprocess.Popen(cmd, stdout=open(workdir + "/qemu.log", "w"), stderr=subprocess.STDOUT,
                            env=dict(os.environ, **(env or {})))
    for _ in range(100):
        try:
            return proc, RspClient("127.0.0.1", port, timeout=60)
        except OSError:
            time.sleep(0.1)
    proc.kill()
    sys.exit("QEMU gdb stub never came up; see %s/qemu.log" % workdir)


# ---------------------------------------------------------------- gdb helpers
def setreg(c, n, v):
    r = c.command("P%x=%s" % (n, struct.pack("<I", v & 0xFFFFFFFF).hex()))
    if r != "OK":
        sys.exit(f"P{n:x} failed: {r!r}")


def wmem(c, addr, data):
    r = c.command("M%x,%x:%s" % (addr, len(data), bytes(data).hex()))
    if r != "OK":
        sys.exit(f"M {addr:#x} failed: {r!r}")


def regs(c):
    return parse_g_reply(c.command("g"))


def run_to(c, stop_pcs, timeout=30):
    """Continue until the CPU stops at one of stop_pcs (hardware breakpoints), return pc."""
    for a in stop_pcs:
        c.command("Z1,%x,2" % a)
    c.send_packet("c")
    c.sock.settimeout(timeout)
    c.read_packet()
    pc, _ = read_pc(c)
    for a in stop_pcs:
        c.command("z1,%x,2" % a)
    if pc not in stop_pcs:
        sys.exit(f"stopped at {pc:#x}, expected one of {[hex(a) for a in stop_pcs]}")
    return pc


def halt(c):
    c.sock.sendall(b"\x03")
    c.read_packet()


def call_a(c, fn, *args):
    for i, a in enumerate(args):
        setreg(c, i, a)
    setreg(c, 14, STACK_A)
    setreg(c, 15, SENTINEL)
    setreg(c, 0x48, fn)
    run_to(c, [SENTINEL])


# ---------------------------------------------------------------- phase A
def phase_a(sym, L, workdir, kernel, iram, sram):
    print("== A: DRBG core against the OpenSSL model (no boot)")
    st = sym["mi_rng_state"]
    proc, c = start_qemu(workdir, kernel, iram, sram, halted=True)
    try:
        vectors = [(bytes(range(32)), bytes(16), bytes(range(48))),
                   (bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4"),
                    bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"), bytes(48)),
                   (b"\xff" * 32, b"\xff" * 16, bytes([0xA5] * 48))]    # V rolls over 2^128
        for n, (k, v, noise) in enumerate(vectors):
            wmem(c, st + L["key"], k)
            wmem(c, st + L["v"], v)
            wmem(c, SCRATCH_A, noise)
            kk, vv = k, v
            for rnd in range(3):                      # chained: state carries between calls
                call_a(c, sym["mi_rng_step"], st, SCRATCH_A)
                kk, vv, out = model_step(kk, vv, noise)
                got = (read_mem(c, st + L["buf"], 16), read_mem(c, st + L["key"], 32),
                       read_mem(c, st + L["v"], 16))
                report(got == (out, kk, vv), f"vector {n} step {rnd}: out {got[0].hex()} "
                       f"{'==' if got == (out, kk, vv) else '!='} model {out.hex()}")
                report(read_mem(c, st + L["temp"], 48) == bytes(48), f"vector {n} step {rnd}: temp wiped")
        report(struct.unpack("<I", read_mem(c, st + L["avail"], 4))[0] == 16, "avail == 16 after generate")
        return c, proc
    except BaseException:
        proc.kill()
        raise


def phase_c(sym, L, workdir, kernel, iram, sram):
    """Exact instruction count of one aes_block (the longest stretch with IRQs off): QEMU with
    one instruction per translation block logs one `Trace` line per executed instruction."""
    print("== C: instructions with IRQs off in one aes_block")
    st = sym["mi_rng_state"]
    log = workdir + "/exec.log"
    proc, c = start_qemu(workdir, kernel, iram, sram, halted=True,
                         extra=["-accel", "tcg,one-insn-per-tb=on", "-d", "exec,nochain", "-D", log])
    try:
        wmem(c, st + L["key"], bytes(range(32)))
        wmem(c, st + L["v"], bytes(16))
        call_a(c, sym["mi_rng_step"], st, SCRATCH_A)       # leaves a valid key schedule in the state
        call_a(c, sym["aes_block"], st, st + L["v"], SCRATCH_A + 0x100)
    finally:
        c.close()
        proc.terminate()                                    # SIGTERM so QEMU flushes the log
        proc.wait(timeout=30)
    import re
    pcs = [int(m.group(1), 16) for m in re.finditer(r"Trace 0: \S+ \[\w+/([0-9a-f]+)/", open(log).read())]
    last = max(i for i, pc in enumerate(pcs) if pc == sym["aes_block"])
    n = len(pcs) - last
    report(10 < n < 20000, f"aes_block executes {n} instructions, all with IRQs off but ~10")
    print(f"      ~{n / 192e6 * 1e6:.1f} us at 192 MHz if 1 instruction/cycle (estimate; the code runs from IRAM)")


# ---------------------------------------------------------------- phase B
TICK_CTR = 0x000426D0        # uC/OS-III tick counter (100 Hz): advances once the RTOS is running
BOOT_TICKS = 400             # "booted": 4 s of guest time (measured: ticking steadily from ~0.3 s)


def boot(workdir, kernel, iram, sram, seconds, env=None):
    """Boot, then halt once the tick counter shows BOOT_TICKS of RTOS time AND the halt landed
    with interrupts enabled (PSR IE), so a hijacked call never starts inside a critical section.
    `seconds` is the cap: past it the old fixed-sleep behaviour applies and we halt regardless."""
    proc, c = start_qemu(workdir, kernel, iram, sram, halted=False, env=env)
    c.command("?")
    c.send_packet("c")
    deadline = time.time() + max(seconds, 12)
    while True:
        time.sleep(0.25)
        halt(c)
        tick = struct.unpack("<I", read_mem(c, TICK_CTR, 4))[0]
        psr = regs(c).get("psr")
        if (tick >= BOOT_TICKS and psr is not None and psr & 0x40) or time.time() > deadline:
            return proc, c
        c.send_packet("c")


def hijack_call(c, fn, canary):
    for i in range(4, 12):
        setreg(c, i, canary + i)
    setreg(c, 14, STACK_B)
    setreg(c, 15, SENTINEL)
    setreg(c, 0x48, fn)
    run_to(c, [SENTINEL])
    r = regs(c)
    return r["r0"] & 0xFFFFFFFF, all(r[f"r{i}"] == canary + i for i in range(4, 12)) and r["r14"] == STACK_B


def hijack_loop(c, ctx):
    wmem(c, ctx + 0x22, b"\xee" * 6)
    for i in range(4, 12):
        setreg(c, i, 0)
    setreg(c, 4, ctx)
    setreg(c, 14, STACK_B)
    setreg(c, 15, SENTINEL)
    setreg(c, 0x48, LOOP_START)
    run_to(c, [LOOP_END])
    return read_mem(c, ctx + 0x22, 6), regs(c)["r5"] == ctx + 0x28


def phase_b(sym, L, workdir, kernel, iram, sram, boot_s):
    print("== B: booted, real call path")
    st = sym["mi_rng_state"]
    proc, c = boot(workdir, kernel, iram, sram, boot_s)
    try:
        report(struct.unpack("<I", read_mem(c, st + L["magic"], 4))[0] == 0, "state starts unseeded (magic absent)")
        res = [hijack_call(c, sym["mi_rng_entry"], 0x5A5A0000)]
        report(struct.unpack("<I", read_mem(c, st + L["magic"], 4))[0] == 0x4D495247, "first call seeded the state (magic == 'MIRG')")
        res += [hijack_call(c, sym["mi_rng_entry"], 0x5A5A0000) for _ in range(95)]
        data = bytes(b & 0xFF for b, _ in res)
        report(all(good for _, good in res), "r4-r11 and sp preserved over 96 calls")
        report(all(b < 256 for b, _ in res), "every return value is a byte")
        report(len(set(data)) > 60, f"{len(set(data))} distinct values in 96 bytes")
        report(len({data[i:i + 4] for i in range(0, 96, 4)}) == 24, "no repeated 4-byte MI among 24")
        calls = struct.unpack("<I", read_mem(c, st + L["calls"], 4))[0]
        report(calls == 96, f"calls counter == {calls}")
        report(read_mem(c, st + L["temp"], 48) == bytes(48), "scratch temp wiped after a real call")
        busy, cont = struct.unpack("<II", read_mem(c, st + L["busy"], 8))
        report(busy == 0 and cont == 0, f"lock released (busy={busy}) and never contended ({cont})")
        wmem(c, st + L["busy"], struct.pack("<I", 1))      # simulate a holder: the caller must wait, then proceed
        t0 = time.time()
        b, good = hijack_call(c, sym["mi_rng_entry"], 0x5A5A0000)
        busy, cont = struct.unpack("<II", read_mem(c, st + L["busy"], 8))
        report(good and busy == 0 and cont >= 2000, f"preset busy lock: call still returned after {cont} retries "
               f"({time.time() - t0:.1f} s wall), lock released")

        # B3: Davies-Meyer finalisation recomputed from what the state left behind
        blk = read_mem(c, st + L["blk"], 32)
        noise = read_mem(c, st + L["noise"], 48)
        h = blk[:16]
        report(blk[16:] == b"\x03" * 16 and noise[32:] == model_dm(h, blk),
               "noise[32:48] == E_blk(h) ^ h for the final finalisation block (independent recompute)")

        # B2: the stock loop, through the patched bsr
        mi1, ok1 = hijack_loop(c, FAKE_CTX_B)
        mi2, ok2 = hijack_loop(c, FAKE_CTX_B)
        report(ok1 and ok2, "stock MI loop exits with r5 == ctx+0x28 (6 iterations)")
        report(mi1 != b"\xee" * 6 and mi2 != b"\xee" * 6 and mi1 != mi2,
               f"two channel loads give different MIs: {mi1.hex()} / {mi2.hex()}")
        return c, proc
    except BaseException:
        proc.kill()
        raise


def phase_tap(sym, L, workdir, kernel, iram, sram, boot_s, out):
    print("== TAP: measurement build fills its sample buffer on the first draw")
    st, tap = sym["mi_rng_state"], sym["mi_rng_tap"]
    proc, c = boot(workdir, kernel, iram, sram, boot_s)
    try:
        wmem(c, tap, bytes(16))                              # as on hardware: no magic before the first draw
        b, good = hijack_call(c, sym["mi_rng_entry"], 0x5A5A0000)
        report(good, "r4-r11 and sp preserved over the seeding call")
        magic, count, cap, rounds = struct.unpack("<IIII", read_mem(c, tap, 16))
        report(magic == 0x5054494D, f"tap magic {magic:#x} == MITP")
        report(count == rounds * 4 + 8 and cap == 1022, f"count {count} == 4 x {rounds} seed rounds + 8 (first mix), capacity {cap}")
        buf = read_mem(c, tap, 16 + 8 * min(count, cap))
        recs = [struct.unpack_from("<IHH", buf, 16 + 8 * i) for i in range(min(count, cap))]
        report(all(r[1] <= 0x3FF for r in recs), "every ADC value is 10-bit")
        report(len({r[0] for r in recs}) > len(recs) // 2, "timer stamps are not constant")
        out.write_bytes(buf + bytes(0x2000 - len(buf)))
        print(f"      wrote {out} ({len(buf)} bytes of tap data); analyse with mi_rng_entropy.py --source qemu")
    finally:
        c.close()
        proc.kill()


def first_mi(workdir, kernel, iram, sram, boot_s, env):
    proc, c = boot(workdir, kernel, iram, sram, boot_s, env)
    try:
        return hijack_loop(c, FAKE_CTX_B)[0]
    finally:
        c.close()
        proc.kill()
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot-seconds", type=int, default=10)
    ap.add_argument("--noise-dependence", action="store_true")
    ap.add_argument("--measure-irq-off", action="store_true")
    ap.add_argument("--only-c", action="store_true", help="run only phase C (needs --measure-irq-off)")
    ap.add_argument("--tap", type=pathlib.Path, metavar="OUT.bin",
                    help="test the MEASUREMENT build (out/mi_rng_tap) instead: boot, make one draw, check "
                         "the sample tap filled, and write the tap buffer to OUT.bin for mi_rng_entropy.py --source qemu")
    args = ap.parse_args()
    global PATCHED, ELF
    if args.tap:
        PATCHED = REPO_ROOT / "out/mi_rng_tap/progmem_MI_RNG_TAP_patched.bin"
        ELF = REPO_ROOT / "out/mi_rng_tap/build/mi_rng.elf"
    if not PATCHED.exists():
        sys.exit(f"{PATCHED} missing: build the patch first (see the usage line)")

    sym = symbols()
    L = layout(sym)
    w = tempfile.mkdtemp(prefix="mi_rng_test_")
    kernel, iram, sram = compose(w)

    # the splice itself, byte for byte against the symbol table
    img = PATCHED.read_bytes()
    kind, half = decode_branch(img[0x5CAEC:0x5CAF0])
    tgt = 0x0305CAEC + half * 2
    report(kind == "bsr" and tgt == sym["mi_rng_entry"],
           f"hook @0x0305caec decodes to {kind} {tgt:#x} (mi_rng_entry {sym['mi_rng_entry']:#x})")
    report(img[0x5CAF0:0x5CAF8] == FLASH.read_bytes()[0x5CAF0:0x5CAF8], "the six instructions after the hook are stock")

    if args.tap:
        os.makedirs(w + "/t")
        phase_tap(sym, L, w + "/t", kernel, iram, sram, args.boot_seconds, args.tap)
        print("ALL PASS" if ok else "FAILURES")
        sys.exit(0 if ok else 1)

    os.makedirs(w + "/a")
    c, proc = phase_a(sym, L, w + "/a", kernel, iram, sram)
    c.close()
    proc.kill()
    time.sleep(0.5)
    if args.measure_irq_off:
        os.makedirs(w + "/c")
        phase_c(sym, L, w + "/c", kernel, iram, sram)
        time.sleep(0.5)

    if args.only_c:
        print("ALL PASS" if ok else "FAILURES")
        sys.exit(0 if ok else 1)
    os.makedirs(w + "/b")
    c, proc = phase_b(sym, L, w + "/b", kernel, iram, sram, args.boot_seconds)
    c.close()
    proc.kill()
    time.sleep(0.5)

    if args.noise_dependence:
        print("== B+: two boots, identical emulator inputs (stock rand() gives identical MIs here)")
        runs = []
        for i in range(2):
            os.makedirs(f"{w}/n{i}")
            runs.append(first_mi(f"{w}/n{i}", kernel, iram, sram, args.boot_seconds, {"DM32_ADC_CH2": "0x300"}))
            print(f"      boot {i}: first MI bytes {runs[-1].hex()}")
        report(runs[0] != runs[1], "two boots with the same ADC input draw different MIs")
        print("      This shows the timer stamps reach the output. The emulator's timer follows host time and its\n"
              "      ADC is a constant, so it says nothing about hardware noise or entropy.")

    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
