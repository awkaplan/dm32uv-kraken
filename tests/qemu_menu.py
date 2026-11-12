"""Booted QEMU runs of the combined image, driven through the stock menu: the harness every e2e script and tests/optrow_checks.py share.

  Boot(patched, tag, seq, port=None, bp=None, settle=100, on_hit=None)
        one booted run: qemu_run.py replays the key sequence `seq` (keys()), a gdb stub on a free port reads memory.
        on_hit(boot): called at every breakpoint hit while the guest is stopped there (read registers with boot.regs(),
        memory with boot.mem_halted()), e.g. to capture what a stock call is handed.
        .mem(addr, n) / .u8 / .u16 / .u32   read (halts the guest for the read, attach semantics)
        .poke(addr, data)                    write
        .until(pred, timeout)                poll pred(self) while pumping; DM32_TIMEOUT_SCALE multiplies every timeout
        .wait_step(n) / .pump(s) / .hits     key delivery, breakpoint hit counting (bp=)
        the menu-cell readers: .cells() .read_list() .labels_of(n) .in_list(mode) .at_depth(depth, icon) .wait_list(contains)
  keys(seq, gap=400, fast_gap=None), KEY, PATHS     the key table and the menu walks that reach each list
  run_scenarios(patched, scen, report)              parallel batches of Boots, one checker thread each
  Suite                                             PASS/FAIL printing, the failure list and the exit code of an e2e main()
  the RAM addresses of the stock menu state (CELL_*, HIST, UI_MODE, L2/L3 lists, OS_TICKS) live here too: one home.

Trap every scenario is written around: wait_step() reports a key as SENT, not DISPATCHED, so every read is taken after
b.until(<a predicate identifying the level we want>), never straight after the walk.
"""
import glob
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

from build_artifacts import COMPOSE, DATACHIP, RE_TOOLS
from gdb_rsp import RspClient, parse_g_reply, read_mem

# ------------------------------------------------------------------------------------------------------------------
# RAM of the stock menu (docs/stock-option-level.md, docs/nested-submenu.md)
CELL_COUNT, CELL_PTR, CELL_SUB, CELL_VIS, CELL_IDX = 0x3E8F8, 0x3E904, 0x18000A90, 0x3E8F4, 0x3E5F8
CELL_ICON, CELL_CUR, CURSOR, SLOT = 0x32AB6, 0x3E91F, 0x3E5CC, 0x3E5D5
HIST = 0x320AC                      # {depth, depth, depth+1, mode code}; the mode advances by 7 per level
UI_MODE = HIST + 3                  # the mode code: 0x0e KRAKEN/APRS list, 0x15 level-3 list, 0x1c/0x23/0x2a option levels
TOP, TOPROW = 0x3E641, 0x3E4E9      # top-level list of the open category (item per row) and its highlighted row
L2, ROW = 0x3E664, 0x3E4EA          # level-2 list and its row (ROW is also the KRAKEN screen row)
L3_LIST, L3_ROW = 0x3E687, 0x3E4EB  # level-3 copy of the index list (the Channel Set rows) and its row
EDIT_BUF, EDIT_LEN, EDIT_CUR, EDIT_FLAGS, EDIT_FRESH = 0x3EE84, 0x3EDF6, 0x3EE72, 0x3E31E, 0x320B0   # the stock text entry
OS_TICKS = 0x426D0                  # uC/OS-III tick counter (100 Hz)

# ------------------------------------------------------------------------------------------------------------------
# keys: "scancode event" tuples for qemu_run.py --keyseq; {g} is the gap after the key in ms
KEY = {"menu": "-1:0:1:17:20:120", "lead": "-1:0:1:31:0:20", "up": "2:11:1:17:60:{g}", "down": "2:11:1:18:60:{g}", "ok": "2:7:1:17:60:{g}", "back": "2:10:1:17:60:{g}",
       "hold": "2:7:1:17:4000:{g}",   # OK held 4000 scans: Freq Unlock's hold-OK warning wants 10 s on the OS tick (docs/freq-unlock.md);
                                      # a hold is counted in key scans, not ms: ~16 s with the idle hook sleeping, ~26 s spinning
       "k1": "2:7:1:20:60:{g}", "k2": "2:7:1:19:60:{g}", "k3": "2:7:1:18:60:{g}", "k4": "2:8:1:20:60:{g}", "k5": "2:8:1:19:60:{g}", "k6": "2:8:1:18:60:{g}"}

# the walks from standby to each list the e2e scripts work in (analog VFO A)
PATHS = {
    "cs": "lead menu down ok down ok",              # Menu > Settings (one Down from Scan) > OK > Channel Set (one Down from Radio Settings) > OK: level-3 list, depth 3, mode 0x15
    "rs": "lead menu down ok ok",                   # Menu > Settings > OK > Radio Settings (item 0, already selected): level-3 list
    "of": "lead menu down ok ok down down down ok",  # ... > Other Func (row 3) > OK: the stock Other Func list, depth 4
    "krk": "lead menu up up ok",                    # KRAKEN list (analog VFO A: Scan, Settings, Call Log, APRS, KRAKEN, GNSS)
    "aprs": "lead menu down down ok",               # APRS list (analog)
}


class KeySeq(str):
    """The qemu_run.py key string, plus `.guards`: {step index: (ticks, predicate, text)} for the gaps that wait on the guest, not
    on key scans (Boot.wait_step releases them; see keys())."""
    guards = {}


GUARD_READS = 10 ** 12      # the gap of a guarded step: it never ends by itself, Boot.release_step() ends it


def keys(seq, gap=400, fast_gap=None, conds=None):
    """A key sequence string for qemu_run.py. `seq` is space separated names from KEY; `name:g` overrides that key's gap.
    fast_gap: every Down directly followed by another Down uses it instead (a dozen Downs at 400 ms outlast the stock 5 s
    Menu Exit Time and the radio drops to standby mid-walk; the OKs keep the long gap so each level has settled).

    The default gap (no `:g`, the `gap` argument is then unused) is DM32_TICK_GAP guest ticks (default 150 = 1.5 s, 0 = `gap` scans as
    before): the same wait on a quiet and on a loaded host. An explicit numeric gap `g` counts keypad scans (row-bank reads), NOT time: how many scans the guest gets per second depends on how busy
    the host is (one 2500 gap took 13 s of guest time on a quiet host and 28 s on a loaded one). A gap that has to mean guest
    time or a guest state is written so instead, and the next key is then sent only when it holds:
        `ok:t450`      450 guest ticks (uC/OS-III OS_TICKS, 100 Hz: 4.5 s) after the key went down
        `ok:?name`     conds["name"](boot) is true (a predicate on the guest state, e.g. "back in the Channel Set list")
        `ok:?name:t60` both (the state AND at least 60 ticks since the key went down)
    Boot.pump releases the guarded gaps (wait_step, until and wait_list all pump); a Boot that never pumps stalls at the first one."""
    tick_gap = int(os.environ.get("DM32_TICK_GAP", "150"))
    names = seq.split()
    out, guards = [], {}
    for i, k in enumerate(names):
        name, _, g = k.partition(":")
        if g.startswith(("?", "t")):
            ticks, cond = 0, None
            for part in g.split(":"):
                if part.startswith("t"):
                    ticks = int(part[1:])
                else:
                    cond = (conds or {})[part[1:]]
            guards[i] = (ticks, cond, g)
            g = GUARD_READS
        elif not g and tick_gap and not (fast_gap and name == "down" and names[i + 1:i + 2] == ["down"]):
            guards[i] = (tick_gap, None, f"t{tick_gap}")       # the default gap is in guest ticks (DM32_TICK_GAP, 0 = key scans as before)
            g = GUARD_READS
        elif not g and fast_gap and name == "down" and names[i + 1:i + 2] == ["down"]:
            g = fast_gap
        out.append(KEY[name].format(g=g or gap))
    ks = KeySeq(";".join(out))
    ks.guards = guards
    return ks


_ports = set()
_ports_lock = threading.Lock()


def free_port():
    """A TCP port nobody listens on (bind port 0), never handed out twice by this process."""
    with _ports_lock:
        while True:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            if port not in _ports:
                _ports.add(port)
                return port


def timeout_scale():
    """DM32_TIMEOUT_SCALE: a multiplier on every wait, for a host that is running other builds."""
    return float(os.environ.get("DM32_TIMEOUT_SCALE", "1"))


# The stock idle task spins: OS_IdleTask (IRAM 0x10868) loops through two measured critical sections (CPU_TS_TmrRd,
# OS_SchedLockTimeMeas*) and OSIdleTaskHook, and QEMU emulates every pass - a PC sample of a radio at standby was ~75%
# in that bookkeeping, one host core per QEMU, so six parallel boots starved each other into timing failures
# (2026-11-06). OSIdleTaskHook (IRAM 0x106cc) only calls OS_AppIdleTaskHookPtr (0x426c4), which stock never sets
# (read 0 after boot), so in the QEMU images of the booted runs its body becomes `wait; jmp r15`: the CPU sleeps until
# the next interrupt (QEMU's helper_wait halts the vCPU) with interrupts enabled, as they are at that call. Only the
# test images are touched. DM32_IDLE_SPIN=1 keeps the stock spin.
IDLE_HOOK = 0x000106CC
IDLE_HOOK_STOCK = bytes.fromhex("d0147512609303e90300cd7b9014")    # push r15; lrw r3; ld.w r3,(r3); bez r3; jsr r3; pop r15
IDLE_HOOK_WAIT = bytes.fromhex("00c0204c3c78")                      # wait; jmp r15
IDLE_FILES = (("boot.bin", IDLE_HOOK + 0x030AFC20 - 0x03000000),    # the flash copy the boot stub loads IRAM from (+0x030afc20)
              ("iram.bin", IDLE_HOOK - 0x00010000))                 # compose_qemu_image's IRAM image (base 0x10000)


def idle_wait(d):
    """Patch the idle hook of the composed QEMU image in directory d to sleep (see IDLE_HOOK). False if left as stock."""
    if os.environ.get("DM32_IDLE_SPIN") == "1":
        return False
    imgs = [(d / f, off, bytearray((d / f).read_bytes())) for f, off in IDLE_FILES]
    if any(b[off:off + len(IDLE_HOOK_STOCK)] != IDLE_HOOK_STOCK for _, off, b in imgs):
        print(f"     note: the idle hook at {IDLE_HOOK:#x} is not stock's bytes; the booted run keeps the idle spin")
        return False
    for p, off, b in imgs:
        b[off:off + len(IDLE_HOOK_WAIT)] = IDLE_HOOK_WAIT
        p.write_bytes(bytes(b))
    return True


_seq_reads_cache = {}


_has_keyseq_prop = {}


def _qemu_exe(port):
    """The binary of the QEMU whose gdb stub listens on `port`."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            cl = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if f"tcp::{port}".encode() in b" ".join(cl) and b"qemu-system" in cl[0]:
            return os.path.realpath(f"/proc/{pid}/exe")
    raise RuntimeError(f"no QEMU with gdb port {port}")


def has_keyseq_property(port):
    """Does this QEMU have the runtime key-sequencer properties (dm32uv-qemu main, docs/key-injection.md)?"""
    exe = _qemu_exe(port)
    if exe not in _has_keyseq_prop:
        _has_keyseq_prop[exe] = b"keyseq-remaining" in open(exe, "rb").read()
    return _has_keyseq_prop[exe]


def _qemu_seq_reads(port):
    """(pid, address) of the dm32_seq_reads counter of the QEMU whose gdb stub listens on `port` (symbol from the binary, plus the load
    address of a position independent one)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            cl = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if f"tcp::{port}".encode() in b" ".join(cl) and b"qemu-system" in cl[0]:
            exe = os.path.realpath(f"/proc/{pid}/exe")
            if exe not in _seq_reads_cache:
                nm = subprocess.run(["nm", exe], capture_output=True, text=True, check=True).stdout
                off = int(next(l.split()[0] for l in nm.splitlines() if l.endswith(" dm32_seq_reads")), 16)
                pie = open(exe, "rb").read(18)[16:18] == b"\x03\x00"       # ELF e_type ET_DYN
                _seq_reads_cache[exe] = (off, pie)
            off, pie = _seq_reads_cache[exe]
            base = 0
            if pie:
                base = next(int(l.split("-")[0], 16) for l in open(f"/proc/{pid}/maps") if l.rstrip().endswith(exe))
            return int(pid), base + off
    raise RuntimeError(f"no QEMU with gdb port {port}")


class Boot:
    """One booted run of the combined image: qemu_run.py drives the keys, the gdb stub (own port) reads memory."""

    def __init__(self, patched, tag, seq, port=None, bp=None, settle=100, on_hit=None):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix=f"qemu_{tag}_"))
        d = self.dir
        subprocess.run([*COMPOSE, "--patched", str(patched), "--out", str(d / "boot.bin"), "--iram-out"],
                       check=True, stdout=subprocess.DEVNULL)
        idle_wait(d)
        subprocess.run(f"xz -dc {DATACHIP} > {d / 'datachip.bin'}", shell=True, check=True)
        pd = open(patched, "rb").read()
        sram = bytearray(0x8000)
        init = pd[0x72000:0x763FC]
        sram[0x800:0x800 + len(init)] = init
        (d / "sram.bin").write_bytes(bytes(sram))
        self.nsteps = len(seq.split(";"))
        self.guards = dict(getattr(seq, "guards", {}))      # {step: (ticks, predicate, text)}, released by wait_step
        self._seen = {}                                     # guarded step -> OS_TICKS when it was first seen
        self.port = port or free_port()
        cmd = [sys.executable, "-u", str(RE_TOOLS / "qemu_run.py"), "--image", str(d / "boot.bin"), "--iram", str(d / "iram.bin"), "--sram", str(d / "sram.bin"),
               "--rundir", str(d / "run"), "--until-step", str(self.nsteps - 1), "--settle-after", str(settle), "--timeout", "420", f"--keyseq={seq}",
               "--spinor", str(d / "datachip.bin"), "--extra", f"-gdb tcp::{self.port}"]
        self.log = open(d / "qr.log", "w")
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT, cwd=str(RE_TOOLS))
        self._qom_release = None                          # unknown until the first release: see release_step
        self.c = None
        for _ in range(600):
            try:
                self.c = RspClient("127.0.0.1", self.port, timeout=60)
                break
            except OSError:
                time.sleep(0.2)
        if self.c is None:
            raise RuntimeError(f"boot {tag}: gdb stub never came up")
        # TCP_NODELAY (as target.nodelay): every read halts the guest, and Nagle + delayed ACK kept it halted ~50 ms per packet
        self.c.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.hits = 0
        self.bp = bp
        self.on_hit = on_hit
        if bp:
            self.c.command("Z1,%x,2" % bp)
        self.running = False
        self._go()

    def _go(self):
        try:
            self.c.send_packet("c")
        except OSError:                                 # QEMU already ended (qemu_run's settle period is over)
            pass
        self.running = True

    def _halt(self):
        self.c.sock.sendall(b"\x03")
        self.c.sock.settimeout(10)
        try:
            self.c.read_packet()
        except Exception:
            pass
        self.c.sock.settimeout(60)

    def step(self):
        qlog = self.dir / "run" / "qemu.log"
        try:
            return max([int(x) for x in re.findall(r"KEYSEQ -> step (\d+)", qlog.read_text(errors="ignore"))] or [-1])
        except OSError:
            return -1

    def pump(self, seconds):
        """Let the guest run for `seconds`, counting stops at the breakpoint (each one is resumed). While a guarded key gap is
        pending (keys()) it runs in quarter-second slices and releases the gap as soon as its condition holds."""
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if not self.guards:
                self._pump(left)
                return
            if left <= 0:
                return
            self._pump(min(0.25, left))
            self.service_guards()

    def _pump(self, seconds):
        end = time.time() + seconds
        self.c.sock.settimeout(0.5)
        while time.time() < end:
            try:
                self.c.read_packet()                    # a stop reply: the breakpoint hit
            except OSError:
                continue
            except Exception:
                return
            self.hits += 1
            self.c.sock.settimeout(60)
            if self.on_hit:
                self.on_hit(self)
            if self.bp:                                 # step over the breakpoint (it would hit again at once), then continue
                self.c.command("z1,%x,2" % self.bp)
                self.c.command("s")
                self.c.command("Z1,%x,2" % self.bp)
            self._go()
            self.c.sock.settimeout(0.5)

    def wait_step(self, n, timeout=200):
        t0 = time.time()
        timeout *= timeout_scale()
        while self.step() < n and time.time() - t0 < timeout:
            self.pump(1.0)
        return self.step() >= n

    def service_guards(self, left=60.0):  # left: seconds before a condition that never holds is given up on
        """Release the guarded gap (keys(): `ok:t450`, `ok:?name`) the key sequence is sitting in, once the guest has got there.
        A condition that never comes true is released after `left` seconds (the scenario's own check then reports the failure)."""
        st = self.step()
        if st not in self.guards:
            return
        ticks, cond, text = self.guards[st]
        now = self.u32(OS_TICKS)
        first, t_seen = self._seen.setdefault(st, (now, time.time()))
        if now - first >= ticks and (cond is None or cond(self)) or time.time() - t_seen > max(left, 5):
            del self.guards[st]
            self.release_step()

    def release_step(self):
        """End the current key gap now. A QEMU with the key sequencer in device state (dm32uv-qemu main: docs/key-injection.md)
        takes "qom-set /machine keyseq-remaining 0" through the gdb stub's monitor command; the older binary has a static
        dm32_seq_reads (hw/gpio/dm32_gpio.c), set past the step's span through /proc/<pid>/mem (QEMU runs in this container)."""
        if self._qom_release is None:
            self._qom_release = has_keyseq_property(self.port)
        if self._qom_release:
            self._halt()
            r, out = self.c.command("qRcmd," + b"qom-set /machine keyseq-remaining 0".hex()), ""
            while r.startswith("O") and r != "OK":            # monitor output (hex) comes before the final reply
                out += bytes.fromhex(r[1:]).decode(errors="replace")
                r = self.c.read_packet()
            self._go()
            if r != "OK" or out.strip():
                raise RuntimeError(f"keyseq-remaining release failed: {r!r} {out.strip()!r}")
            return
        pid, addr = _qemu_seq_reads(self.port)
        with open(f"/proc/{pid}/mem", "r+b", buffering=0) as f:
            f.seek(addr)
            f.write(struct.pack("<Q", 2 * GUARD_READS))

    def mem(self, addr, n):
        """Stop the guest for the read (attach semantics), then let it run on."""
        self._halt()
        data = read_mem(self.c, addr, n)
        self._go()
        return data

    def poke(self, addr, data):
        """Stop the guest, write memory, let it run on."""
        self._halt()
        r = self.c.command("M%x,%x:%s" % (addr, len(data), bytes(data).hex()))
        if r != "OK":
            raise RuntimeError(f"poke {addr:#x} failed: {r!r}")
        self._go()

    def regs(self):
        """r0..r31 of the stopped guest (inside on_hit)."""
        return parse_g_reply(self.c.command("g"))

    def mem_halted(self, addr, n):
        """A read while the guest is already stopped (inside on_hit)."""
        return read_mem(self.c, addr, n)

    def u8(self, addr):
        return self.mem(addr, 1)[0]

    def u16(self, addr):
        return struct.unpack("<H", self.mem(addr, 2))[0]

    def u32(self, addr):
        return struct.unpack("<I", self.mem(addr, 4))[0]

    def until(self, pred, timeout=25):
        t0 = time.time()
        timeout *= timeout_scale()
        while time.time() - t0 < timeout:
            self.pump(1.0)
            try:
                if pred(self):
                    return True
            except Exception:
                pass
        return False

    # ---- the menu cells (the stock list/level state the generators fill)
    def cells(self):
        h = self.mem(HIST, 4)
        return dict(count=self.u8(CELL_COUNT), sub=self.u8(CELL_SUB), icon=self.u8(CELL_ICON), cur=self.u8(CURSOR), dot=self.u8(CELL_CUR),
                    slot=self.u8(SLOT), depth=h[0], mode=h[3])

    def read_list(self):
        """The index list the stock generator built, as [item, ...] (its position in the list is NOT its item number: the
        21-arm visibility chain hides rows)."""
        n = min(self.u16(CELL_IDX), 33)
        return list(self.mem(CELL_IDX + 2, n)) if n else []

    def labels_of(self, count):
        ptr = self.u32(CELL_PTR)
        out = []
        for k in range(min(count, 33)):
            p = struct.unpack("<I", self.mem(ptr + 4 * k, 4))[0]
            out.append(self.mem(p, 24).split(b"\0")[0] if 0x03000000 <= p < 0x03100000 else b"?")
        return out

    def in_list(self, mode=0x0E):
        """The numbered list of mode `mode` is the active level (0x0e: the KRAKEN / APRS list, 0x15: a level-3 list)."""
        return self.u8(CELL_ICON) == 1 and self.u8(UI_MODE) == mode

    def at_depth(self, depth, icon):
        """The level we walked to: the stock history depth AND the row-icon mode (1 = a numbered sub-list, 2 = a radio-mark option level)."""
        return self.u8(HIST) == depth and self.u8(CELL_ICON) == icon

    def wait_list(self, contains=None, timeout=60):
        """Wait for the generator to FILL the index list. The depth and icon cells are written before it has, and a read taken
        straight after them has seen a one-entry list (2026-11-05, 2026-11-06): wait for a row that is only there once it is done."""
        if contains is None:
            return self.until(lambda b: len(b.read_list()) > 1, timeout=timeout)
        return self.until(lambda b: contains in b.read_list(), timeout=timeout)

    def last_frame(self):
        fr = sorted(glob.glob(str(self.dir / "run" / "gram.raw.[0-9]*")))
        d = open(fr[-1], "rb").read()
        return [[struct.unpack_from("<H", d, 2 * (y * 256 + x))[0] for x in range(240)] for y in range(320)]

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        for pid in os.listdir("/proc"):                  # qemu_run.py does not reap its QEMU when it is killed: find it by its gdb port
            if pid.isdigit():
                try:
                    cl = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore")
                except OSError:
                    continue
                if f"tcp::{self.port}" in cl and int(pid) != os.getpid():
                    try:
                        os.kill(int(pid), 9)
                    except OSError:
                        pass
        self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def run_scenarios(patched, scen, report, batch=6, only=None, conds=None):
    """Run booted scenarios in parallel batches and report their results in scen order.

    scen: {name: (key sequence string, breakpoint or None, check(boot, rep))}. Every scenario gets its own Boot and its own
    thread: the key delivery is waited for, then check(b, rep) reads the guest and calls rep(ok, msg). A scenario that raises is a
    failure, not a hang. More parallel QEMUs than the host can run in real time starve the guest (the stock menu exit timer),
    hence the batches. only: an iterable of scenario names to run (default all). conds: {name: predicate(boot)} for the `ok:?name` gaps of keys(). Returns {name: [(ok, msg)]}."""
    names = [n for n in scen if not only or n in only]
    results, times = {}, {}
    slots = threading.Semaphore(batch)                  # a pool, not batches: a scenario waiting out a timeout no longer idles the others' slots

    def work(name):
        res = results[name] = []
        rep = lambda ok, msg: res.append((bool(ok), msg))
        with slots:
            t0 = time.time()
            b = None
            try:
                seq, bp, check = scen[name]
                b = Boot(patched, name.replace(":", "_").replace(" ", ""), keys(seq, conds=conds), bp=bp)
                rep(b.wait_step(b.nsteps - 1), f"[{name}] all {b.nsteps} key steps delivered")
                t1 = time.time()
                check(b, rep)
                times[name] = (t1 - t0, time.time() - t1)
            except Exception as e:                      # a crashed scenario is a failure, not a hang
                rep(False, f"[{name}] scenario error: {type(e).__name__}: {e}")
            finally:
                if b is not None:
                    b.close()
    th = [threading.Thread(target=work, args=(n,)) for n in names]
    for x in th:
        x.start()
    for x in th:
        x.join()
    for name in names:
        for ok, msg in results.get(name, []):
            report(ok, msg)
    if os.environ.get("DM32_TIMINGS") == "1":           # per scenario: boot + key walk, then the checks
        for name in sorted(times, key=lambda n: -sum(times[n])):
            print(f"     time {name}: walk {times[name][0]:.0f}s, checks {times[name][1]:.0f}s")
    return results


class Suite:
    """PASS/FAIL reporting for an e2e main(): prints each check, collects the failures, turns them into the exit code.

    outer: the in-process caller's report function (tests/kraken_ui_test.py passes m.report): when given, results go to it
    (it prints) and only the failure list is kept here."""

    def __init__(self, outer=None):
        self.outer = outer
        self.fails = []

    def report(self, ok, msg, label=None):
        if self.outer:
            self.outer(ok, msg)
        else:
            print(f"{'PASS' if ok else 'FAIL'}  {msg}", flush=True)
        if not ok:
            self.fails.append(label or msg)

    __call__ = report

    def finish(self):
        """Print the verdict and return the exit code."""
        print(f"\n{'ALL PASS' if not self.fails else 'FAILURES: ' + '; '.join(map(str, self.fails))}", flush=True)
        return 1 if self.fails else 0
