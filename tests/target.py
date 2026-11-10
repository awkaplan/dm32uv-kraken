"""Calls into the patch code on a HALTED QEMU machine (-M dm32, no boot) over the gdb stub.

  halted_machine(patched, port=None)   context manager: composes the image, starts QEMU halted, yields the gdb client. A `port`
                                       overrides DM32_GDB_PORT (which tools/build_image.py assigns per test); a second machine
                                       next to the main one needs its own.
  w8/w16/w32, r8/r16/r32, cstr         memory access
  fcall(c, fn, args, fakes, watch)     run fn(*args) with every stock call it reaches in `fakes` intercepted
  call_via(c, fn, args, stop, ...)     run a hook/stub up to one stop address (the displaced stock call), answer it, run on
  count_steps(c, fn, args)             instructions executed by a call, by single-stepping

Every call starts from a clean frame with CANARY in r4-r11 and sp = m.STACK_A and returns through m.SENTINEL, so "the ABI was
kept" is a register comparison afterwards (abi_ok).
"""
import contextlib
import os
import socket
import struct
import subprocess
import sys
import tempfile
import types

import mi_rng_test as m
from build_artifacts import COMPOSE, RE_TOOLS
from gdb_rsp import read_mem

CANARY = 0x5A5A0000


# ---- memory
def w8(c, a, v):
    m.wmem(c, a, bytes([v & 0xFF]))


def open_category(c, cat=10):
    """The menu state of an open top-level category CAT (10 = KRAKEN; 6 = Record, which krk_opt_title / krk_opt_ok turn
    away). The category is the top-level list 0x3e641 at the highlighted row 0x3e4e9."""
    w8(c, 0x3E4E9, 0)
    w8(c, 0x3E641, cat)


def w16(c, a, v):
    m.wmem(c, a, struct.pack("<H", v & 0xFFFF))


def w32(c, a, v):
    m.wmem(c, a, struct.pack("<I", v & 0xFFFFFFFF))


def r8(c, a):
    return read_mem(c, a, 1)[0]


def r16(c, a):
    return struct.unpack("<H", read_mem(c, a, 2))[0]


def r32(c, a):
    return struct.unpack("<I", read_mem(c, a, 4))[0]


def cstr(c, a, n=40):
    return read_mem(c, a, n).split(b"\0")[0] if a else None


# ---- the machine
def nodelay(c):
    """TCP_NODELAY on the gdb socket: without it every packet round trip costs ~50 ms (Nagle + delayed ACK), a target call 0.8 s instead of 3 ms."""
    c.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


@contextlib.contextmanager
def halted_machine(patched, port=None):
    """The combined image composed for QEMU and started halted at reset. Yields the gdb client; QEMU is killed on exit."""
    w = tempfile.mkdtemp(prefix="halted_")
    subprocess.run([*COMPOSE, "--patched", str(patched), "--out", w + "/img.bin", "--iram-out"],
                   check=True, capture_output=True)
    old = os.environ.get("DM32_GDB_PORT")
    if port is not None:
        os.environ["DM32_GDB_PORT"] = str(port)       # m.start_qemu reads its port from here
    try:
        proc, c = m.start_qemu(w, w + "/img.bin", w + "/iram.bin", w + "/sram.bin", halted=True)
    finally:
        if port is not None:
            if old is None:
                os.environ.pop("DM32_GDB_PORT", None)
            else:
                os.environ["DM32_GDB_PORT"] = old
    nodelay(c)
    try:
        yield c
    finally:
        proc.kill()


# ---- calls
def setup_call(c, fn, args=(), sp=m.STACK_A, canary=range(4, 12)):
    """Registers for a call: canary in `canary`, args in r0-r3 (the 5th and later on the stack, C-SKY ABI), sp, lr = SENTINEL, pc = fn."""
    for i in canary:
        m.setreg(c, i, CANARY + i)
    for i, a in enumerate(args[:4]):
        m.setreg(c, i, a)
    if len(args) > 4:
        m.wmem(c, sp, struct.pack("<%dI" % (len(args) - 4), *(a & 0xFFFFFFFF for a in args[4:])))
    m.setreg(c, 14, sp)
    m.setreg(c, 15, m.SENTINEL)
    m.setreg(c, 0x48, fn)


def abi_ok(r, sp=m.STACK_A, canary=range(4, 12), expect=None):
    """r4-r11 and sp as setup_call left them. expect: {reg: value} for registers the code under test legitimately changes or was given."""
    expect = expect or {}
    return all(r[f"r{i}"] == expect.get(i, CANARY + i) for i in canary) and r["r14"] == sp


def fcall(c, fn, args=(), fakes=None, watch=(), limit=6000, sp=m.STACK_A):
    """Calls fn(*args) with a clean frame and canary r4-r11. fakes: {entry: int | callable(regs, r0, r1) -> r0 (or an (r0, r1) tuple)}.
    watch: addresses with a write watchpoint; a hit ends the call (violation True).
    limit: how many intercepted stock calls count as a runaway (a send makes hundreds of os_time_dly_ms calls).
    Returns (stops [(pc, r0, r1)], r0 at return, abi_ok, violation)."""
    fakes = fakes or {}
    setup_call(c, fn, args, sp=sp)
    stops, pcs = [], [m.SENTINEL] + list(fakes)
    for a in pcs:                                   # set once: resuming at the return address never re-hits a breakpoint
        c.command("Z1,%x,2" % a)
    for a in watch:
        c.command("Z2,%x,4" % a)
    try:
        while True:
            c.send_packet("c")
            c.sock.settimeout(120)
            c.read_packet()
            pc, _ = m.read_pc(c)
            if pc not in pcs:
                if watch:
                    return stops, 0, False, True
                sys.exit(f"fcall: stopped at {pc:#x}")
            r = m.regs(c)
            if pc == m.SENTINEL:
                break
            stops.append((pc, r["r0"], r["r1"]))
            v = fakes[pc]
            v = v(r, r["r0"], r["r1"]) if callable(v) else v
            if isinstance(v, tuple):                 # a 64-bit result (soft-float helpers)
                m.setreg(c, 1, v[1])
                v = v[0]
            m.setreg(c, 0, v)
            m.setreg(c, 0x48, r["r15"] & ~1)
            if len(stops) > limit:
                sys.exit("fcall: runaway")
    finally:
        for a in pcs:
            c.command("z1,%x,2" % a)
        for a in watch:
            c.command("z2,%x,4" % a)
    return stops, r["r0"], abi_ok(r, sp=sp), False


def call_via(c, fn, args=(), stop=None, ret=None, skip=False, at_stop=None, regs=None, canary=range(4, 12), sp=m.STACK_A, resume=True):
    """Runs a hook or stub that ends in a call to a stock function at `stop`, and checks what it passed there.

    Starts fn(*args) (regs: {reg: value} preset after the canaries, canary: which registers carry one), runs to `stop` or the return.
    At `stop` the registers are recorded (.at_stop); then ret=<int> makes the stock function "return" that value in r0, skip=True
    makes it return without touching r0, and neither lets the real stock function run. at_stop(regs) is called first (to write
    memory the stock function would have written). Without a `stop` the run just goes to the return. resume=False leaves the machine
    at `stop` (the registers there are .regs) instead of running on to the return.
    Returns a namespace: hit (reached stop), at_stop (registers there), regs (at the return), abi (canary/sp kept)."""
    setup_call(c, fn, args, sp=sp, canary=canary)
    for k, v in (regs or {}).items():
        m.setreg(c, k, v)
    stops = [m.SENTINEL] + ([stop] if stop else [])
    pc = m.run_to(c, stops)
    res = types.SimpleNamespace(hit=pc == stop and stop is not None, at_stop=None)
    if res.hit:
        res.at_stop = m.regs(c)
        if at_stop:
            at_stop(res.at_stop)
        if resume:
            if ret is not None:
                m.setreg(c, 0, ret)
            if ret is not None or skip:
                m.setreg(c, 0x48, res.at_stop["r15"] & ~1)
            pc = m.run_to(c, [m.SENTINEL])
    res.pc = pc
    res.regs = m.regs(c)
    res.abi = (pc == m.SENTINEL or not resume) and abi_ok(res.regs, sp=sp, canary=canary)
    return res


def count_steps(c, fn, args=()):
    """Instructions executed by fn(*args): single-step over the gdb stub until the pc is the return sentinel (0.7 ms per step)."""
    setup_call(c, fn, args)
    n = 0
    while True:
        c.command("s")
        n += 1
        if m.read_pc(c)[0] == m.SENTINEL:
            break
        if n > 3_000_000:
            sys.exit("count_steps: runaway")
    return n, m.regs(c)["r0"]
