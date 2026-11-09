"""Host C compiler helpers for the host tests and the DSP tools.

A beta macOS SDK (MacOSX27, 2026-10) ships a libSystem.tbd the installed linker cannot read, so every
`cc` link fails before a test runs. ensure_sdk() compiles a trivial program once; if that fails, it
points SDKROOT at the newest Command Line Tools SDK that works, for this process and its children.
build() calls it, so a script that only uses build()/run() needs nothing else.

Also the one place for the path constants: HERE (tests/), ROOT, TOOLS, UI (patches/kraken_ui), API (the
dm32uv-re patch API headers, which live next to the repo, not in it: $DM32UV_RE/tools/include, else
../dm32uv-re or ../dm32/dm32uv-re, the same lookup as tools/build.sh).
"""
import os
import pathlib
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TOOLS = ROOT / "tools"
UI = ROOT / "patches/kraken_ui"
def _find_re():
    if os.environ.get("DM32UV_RE"):
        return pathlib.Path(os.environ["DM32UV_RE"])
    for c in (ROOT.parent / "dm32uv-re", ROOT.parent / "dm32/dm32uv-re"):
        if c.is_dir():
            return c
    return ROOT.parent / "dm32uv-re"


API = _find_re() / "tools/include"
_sdk_checked = False


def _links(env):
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "t.c"
        src.write_text("int main(void){return 0;}\n")
        return subprocess.run(["cc", "-o", str(pathlib.Path(d) / "t"), str(src)], env=env,
                              capture_output=True).returncode == 0


def ensure_sdk():
    global _sdk_checked
    if _sdk_checked:
        return
    _sdk_checked = True
    if _links(os.environ):
        return
    for sdk in sorted(pathlib.Path("/Library/Developer/CommandLineTools/SDKs").glob("MacOSX1*.sdk"), reverse=True):
        env = dict(os.environ, SDKROOT=str(sdk))
        if _links(env):
            os.environ["SDKROOT"] = str(sdk)
            print(f"hostcc: default SDK cannot link, using SDKROOT={sdk}")
            return
    raise SystemExit("hostcc: no working macOS SDK found for the host cc")


def build(srcs, flags=("-w", "-O1"), shared=False, driver_src=None, out=None, libs=()):
    """Compile srcs (paths) with the host cc, -I UI and -I API added; returns the output path.

    driver_src: C text written to drv.c in a fresh temp dir and compiled first (the usual inline test driver).
    shared: a -shared -fPIC library (for ctypes). out: output path, default <temp dir>/a.out or lib.so; pass
    flags with "-c" and an out path for an object. flags replaces the default -w -O1. Raises RuntimeError with
    the compiler's stderr tail on failure;"""
    ensure_sdk()
    d = pathlib.Path(tempfile.mkdtemp(prefix="hostcc"))
    srcs = [str(s) for s in srcs]
    if driver_src is not None:
        (d / "drv.c").write_text(driver_src)
        srcs.insert(0, str(d / "drv.c"))
    out = pathlib.Path(out) if out else d / ("lib.so" if shared else "a.out")
    cmd = ["cc", *flags, f"-I{UI}", f"-I{API}"] + (["-shared", "-fPIC"] if shared else []) + ["-o", str(out), *srcs, *libs]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("cc failed: " + r.stderr[-1500:])
    return out


def run(exe, *args, input=None):
    """Run a built driver; returns its stdout (CalledProcessError on a nonzero exit)."""
    return subprocess.run([str(exe), *map(str, args)], input=input, capture_output=True, text=True, check=True).stdout
