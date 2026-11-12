#!/usr/bin/env python3
"""Scaffold a new Kraken feature: a compiling stub, its spec, a host test, a user-guide page and the status row.

  tools/new_feature.py NAME --id F20 --kind setting|screen|oneshot --host kraken|aprs|rs|cs [--label "Text"]
  tools/new_feature.py NAME --remove

Writes (NAME is snake_case, e.g. tone_trim):
  patches/kraken_ui/krk_<name>.c            the stub. setting: a stock option level over a RAM-only value (off at power-on);
                                            screen: a live screen that draws two lines; oneshot: a row that shows a popup.
                                            It never transmits and never touches a stock setting or the data chip.
  patches/kraken_ui/features/<name>.json    the feature spec: id, source, the menu row (appended at the end of its host, so no
                                            existing item number moves), the guide page (docs/menu-hosts.md section 3)
  tests/test_<name>.py                      a host test (hostcc + hostlib) that compiles the stub and checks it
  docs/user-guide/<name-with-dashes>.md     a page with the Status and Where lines the lint wants
  docs/feature-status.md                    one row, status "emulator only"
  patches/kraken_ui/krk_core.c              host aprs only: the row's item is appended to aprs_analog_items[] (that list is explicit)
then runs tools/gen_menu_hosts.py, gen_manifest.py and gen_claims.py. Build with tools/build.sh.

--remove undoes exactly that (a file that no longer carries the scaffold marker is left alone and reported) and regenerates.
Nothing else changes: the stub is yours to rewrite. Add the page to docs/user-guide/README.md and write the developer note
when the feature is real (docs/building.md section 6).
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
UI = REPO / "patches/kraken_ui"
MARK = "scaffold: tools/new_feature.py"
HOSTS = {"kraken": "KRAKEN", "aprs": "APRS", "rs": "Radio Settings", "cs": "Channel Set"}
WHERE = {"kraken": "**Menu > KRAKEN > {l}**",
         "aprs": "**Menu > APRS > {l}** (analog channels only)",
         "rs": "**Menu > Settings > Radio Settings > {l}**",
         "cs": "**Menu > Settings > Channel Set > {l}**"}

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gen_menu_hosts   # noqa: E402


class Fail(Exception):
    pass


def paths(name):
    slug = name.replace("_", "-")
    return {"src": UI / f"krk_{name}.c", "spec": UI / "features" / f"{name}.json", "test": REPO / f"tests/test_{name}.py",
            "page": REPO / f"docs/user-guide/{slug}.md", "status": REPO / "docs/feature-status.md", "core": UI / "krk_core.c",
            "slug": slug}


C_COMMON = '''/*
 * krk_{name}.c - {fid} {label}. {mark}: a stub, rewrite it.
 * {what}
 * It does not transmit and does not touch a stock setting. Rules: CLAUDE.md "Safety rules", docs/menu-hosts.md section 1.
 */
#include "krk.h"
'''

C_SETTING = C_COMMON + '''
/* RAM-only (off at power-on). SRAM is not zeroed at boot, so "on" is a magic, not a 1. A setting that must survive power-off
 * needs a flash record (docs/settings-field.md): do that on purpose, never by default. */
#define ON_MAGIC 0x4b52{magic:04x}u
static u32 state;

static int get(void)
{{
    return state == ON_MAGIC;
}}

static void set(int v)
{{
    state = v ? ON_MAGIC : 0;
}}

static const char *const labels[2] = {{"Off", "On"}};
static const struct krk_opt opt = {{.title = "{label}", .labels = labels, .n = 2, .get = get, .set = set}};

const struct krk_screen krk_{name}_screen = {{
    .label = "{label}",
    .opt = &opt,
}};
'''

C_SCREEN = C_COMMON + '''
static void enter(void)
{{
    krk_line(0, "{label}", KRK_ACCENT);
    krk_line(2, "Stub screen", KRK_FG);
    krk_line(9, "Back: exit", KRK_DIM);
}}

static void tick(u8 scan)
{{
    (void)scan;                         /* every KRK_TICK_MS; Back is the runner's */
}}

const struct krk_screen krk_{name}_screen = {{
    .label = "{label}",
    .enter = enter,
    .tick = tick,
}};
'''

C_ONESHOT = C_COMMON + '''
static void enter(void)
{{
    krk_popup(KRK_POP_INFO, "{label}", "Stub action", POP_RESULT_MS);
}}

const struct krk_screen krk_{name}_screen = {{
    .label = "{label}",
    .enter = enter,
    .flags = KRK_SF_ONESHOT,
}};
'''

WHAT = {"setting": "A setting: the stock option level (Off/On) over a RAM-only value.",
        "screen": "A live screen: the runner owns Back, the keypad and the menu-exit timer.",
        "oneshot": "A one-shot action: runs over the menu and reports with krk_popup()."}

TEST_HEAD = '''#!/usr/bin/env python3
"""Host test for {fid} {label} (krk_{name}.c), {mark}.
Compiles the source with the host cc and drives its struct krk_screen. Replace it with checks of the real logic.
Run: python3 tests/test_{name}.py"""
import hostcc
from hostlib import check, finish

DRV = r\'\'\'
#include <stdio.h>
#include <string.h>
#include "krk_{name}.c"
'''

TEST_SETTING = TEST_HEAD + '''
int main(void) {{
    const struct krk_opt *o = krk_{name}_screen.opt;
    int v0 = o->get();
    o->set(1); int v1 = o->get();
    o->set(0); int v2 = o->get();
    printf("%s|%d|%d|%d|%d\\n", krk_{name}_screen.label, o->n, v1, v2, v0 == 0 || v0 == 1);
    return 0;
}}
\'\'\'
exe = hostcc.build([], flags=["-Wall", "-O1"], driver_src=DRV)
label, n, on, off, sane = hostcc.run(exe).strip().split("|")
check(label == "{label_c}", "the row label")
check(sane == "1", "the state reads Off before anything sets it")
check(n == "2", "two options (Off, On)")
check(on == "1" and off == "0", "set(1) reads back 1, set(0) reads back 0 (RAM only, no flash write)")
finish()
'''

TEST_SCREEN = TEST_HEAD + '''
static int lines;
void krk_line(int n, const char *s, u16 fg) {{ (void)n; (void)s; (void)fg; lines++; }}
int main(void) {{
    krk_{name}_screen.enter();
    krk_{name}_screen.tick(0);
    printf("%d %d\\n", lines, krk_{name}_screen.opt == 0);
    return 0;
}}
\'\'\'
exe = hostcc.build([], flags=["-Wall", "-O1"], driver_src=DRV)
lines, no_opt = hostcc.run(exe).split()
check(int(lines) >= 1, "enter() draws lines")
check(no_opt == "1", "a live screen has no option level")
finish()
'''

TEST_ONESHOT = TEST_HEAD + '''
static int pops;
u8 krk_popup(int kind, const char *l1, const char *l2, unsigned ms) {{ (void)kind; (void)l1; (void)l2; (void)ms; pops++; return 0; }}
int main(void) {{
    krk_{name}_screen.enter();
    printf("%d %d\\n", pops, krk_{name}_screen.flags & KRK_SF_ONESHOT);
    return 0;
}}
\'\'\'
exe = hostcc.build([], flags=["-Wall", "-O1"], driver_src=DRV)
pops, flag = hostcc.run(exe).split()
check(pops == "1", "enter() reports with one popup")
check(int(flag) == 1, "the row is flagged KRK_SF_ONESHOT")
finish()
'''

PAGE = '''# {label}

*Stub written by {mark}. Replace this page when the feature is real: plain steps, every term explained, and for anything
that transmits, when the radio transmits and the legal limits.*

**Status:** emulator only. It has not been tested on a radio.
**Where:** {where}
'''


def run_generators():
    for g in ("gen_menu_hosts.py", "gen_manifest.py", "gen_claims.py"):
        r = subprocess.run([sys.executable, str(REPO / "tools" / g)], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            raise Fail(f"tools/{g} failed:\n{r.stderr.strip()}\nFix it, or undo with: tools/new_feature.py NAME --remove")


def next_slot(spec_rows):
    es = json.loads((UI / "menu_hosts.json").read_text())["ext_slots"]
    used = set(es["hand_written"]) | {r["order"] for f in gen_menu_hosts.load_features() for r in f.get("rows", [])
                                      if r.get("host") == "kraken"}
    free = [k for k in range(es["count"]) if k not in used]
    if not free:
        raise Fail("the KRAKEN list has no free slot (krk_ext_screens[]); see docs/menu-hosts.md")
    return free[0]


def create(a):
    p, name = paths(a.name), a.name
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,24}", name):
        raise Fail("NAME: lower-case letters, digits and _, starting with a letter (e.g. tone_trim)")
    if not re.fullmatch(r"F\d+[a-z]?", a.id or ""):
        raise Fail("--id: an F-number, e.g. F20 (the next free one in docs/feature-status.md)")
    if not a.kind or not a.host:
        raise Fail("--kind and --host are required")
    label = a.label or " ".join(w.capitalize() for w in name.split("_"))
    if len(label) > 16 or not label.isascii():
        raise Fail("--label: ASCII, at most 16 characters (the stock list renderer)")
    for k in ("src", "spec", "test", "page"):
        if p[k].exists():
            raise Fail(f"{p[k].relative_to(REPO)} already exists")
    status = p["status"].read_text()
    if re.search(rf"^\|\s*{a.id}\s*\|", status, re.M):
        raise Fail(f"{a.id} already has a row in docs/feature-status.md")
    if a.host == "aprs" and a.kind == "oneshot":
        pass                        # allowed: the APRS rows take one-shots too
    mh = gen_menu_hosts.merged_spec()
    if a.host == "kraken":
        order = next_slot(None)
    else:
        order = len(next(h for h in mh["hosts"] if h["id"] == a.host)["rows"])
        h = next(h for h in mh["hosts"] if h["id"] == a.host)
        if any(r["key"] == name for r in h["rows"]):
            raise Fail(f"host {a.host} already has a row with key {name}")
        cap = (gen_menu_hosts.num(h["arrays_at"]["en"]) - gen_menu_hosts.num(h["arrays_at"]["zh"])) // 4
        if h["stock"]["count"] + order + 1 > cap:
            raise Fail(f"host {a.host} is full: its two string arrays sit {cap * 4:#x} bytes apart, room for {cap} items. Move "
                       f"arrays_at in menu_hosts.json to free space of the data claim first (docs/menu-hosts.md, \"A full host\"); "
                       f"that moves existing data, so do it on purpose")
    fm = dict(name=name, fid=a.id, label=label, label_c=label, mark=MARK, what=WHAT[a.kind], magic=0)
    # a magic that differs per feature, the same on every run
    fm["magic"] = sum(ord(c) * (i + 7) for i, c in enumerate(name)) & 0xffff
    p["src"].write_text({"setting": C_SETTING, "screen": C_SCREEN, "oneshot": C_ONESHOT}[a.kind].format(**fm))
    p["test"].write_text({"setting": TEST_SETTING, "screen": TEST_SCREEN, "oneshot": TEST_ONESHOT}[a.kind].format(**fm))
    p["test"].chmod(0o755)
    p["page"].write_text(PAGE.format(label=label, mark=MARK, where=WHERE[a.host].format(l=label)))
    row = {"host": a.host, "order": order, "screen": f"&krk_{name}_screen"}
    if a.host != "kraken":
        row.update(key=name, label=label)
    spec = {"feature": a.id, "name": label, "sources": [f"krk_{name}.c"], "rows": [row],
            "guide": f"user-guide/{p['slug']}.md"}
    p["spec"].parent.mkdir(exist_ok=True)
    p["spec"].write_text(json.dumps(spec, indent=1) + "\n")
    # the status row: after the last row of the table
    lines = status.split("\n")
    last = max(i for i, l in enumerate(lines) if l.startswith("| F") or l.startswith("| —"))
    lines.insert(last + 1, f"| {a.id} | {label} | Stub written by tools/new_feature.py: describe it | emulator only | [{label}](user-guide/{p['slug']}.md) |")
    p["status"].write_text("\n".join(lines))
    if a.host == "aprs":
        core = p["core"].read_text()
        new, n = re.subn(r"(aprs_analog_items\[\] = \{[^}]*?)\};", rf"\1, KRK_APRS_ITEM_{name.upper()}}};", core, count=1)
        if n != 1:
            raise Fail("krk_core.c: aprs_analog_items[] not found")
        p["core"].write_text(new)
    print(f"new_feature: wrote krk_{name}.c, features/{name}.json (host {a.host}, order {order}), tests/test_{name}.py, "
          f"docs/user-guide/{p['slug']}.md, a feature-status row" + (", an aprs_analog_items entry" if a.host == "aprs" else ""))
    try:
        run_generators()
    except Fail as e:
        undo(name)
        run_generators()
        raise Fail(f"{e}\nnew_feature: nothing was kept (the scaffold was undone)")


def remove(a):
    undo(a.name)
    print(f"new_feature: removed {a.name}")
    run_generators()


def undo(name):
    p = paths(name)
    if not p["spec"].exists():
        raise Fail(f"no features/{name}.json")
    if p["src"].exists() and MARK not in p["src"].read_text():
        raise Fail(f"krk_{name}.c does not carry the scaffold marker: not a scaffolded feature, nothing removed")
    spec = json.loads(p["spec"].read_text())
    host = spec["rows"][0]["host"] if spec.get("rows") else None
    for k in ("src", "test", "page"):
        f = p[k]
        if not f.exists():
            continue
        if MARK not in f.read_text():
            print(f"new_feature: {f.relative_to(REPO)} no longer carries the scaffold marker: left alone")
            continue
        f.unlink()
    p["spec"].unlink()
    if not any(p["spec"].parent.iterdir()):
        p["spec"].parent.rmdir()
    status = p["status"].read_text()
    kept = [l for l in status.split("\n") if not (l.startswith(f"| {spec['feature']} |") and "Stub written by tools/new_feature.py" in l)]
    p["status"].write_text("\n".join(kept))
    if host == "aprs":
        core = p["core"].read_text()
        p["core"].write_text(core.replace(f", KRK_APRS_ITEM_{name.upper()}}}", "}", 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", metavar="NAME", help="snake_case feature name")
    ap.add_argument("--id", help="feature id, F<number>[a-z], with a row in docs/feature-status.md")
    ap.add_argument("--kind", choices=("setting", "screen", "oneshot"))
    ap.add_argument("--host", choices=sorted(HOSTS), help="where the row appears: kraken (KRAKEN category), aprs, rs (Radio Settings), cs (Channel Set)")
    ap.add_argument("--label", help="the menu text, ASCII, at most 16 characters (default: NAME in words)")
    ap.add_argument("--remove", action="store_true", help="undo what this script created for NAME")
    a = ap.parse_args()
    try:
        remove(a) if a.remove else create(a)
    except (Fail, gen_menu_hosts.SpecError) as e:
        print(f"new_feature: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
