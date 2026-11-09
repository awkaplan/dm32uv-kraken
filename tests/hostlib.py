"""Pass/fail bookkeeping shared by the host tests: check() prints one PASS/FAIL/info line and records hard
failures, finish() prints the verdict and exits 1 if any hard check failed (soft checks never gate)."""
import sys

FAILS = []


def check(cond, label, hard=True, extra=""):
    tag = "PASS" if cond else ("FAIL" if hard else "info")
    if not cond and hard:
        FAILS.append(label)
    print(f"{tag}  {label}" + (f"  [{extra}]" if extra else ""), flush=True)
    return cond


def finish(ok="ALL PASS", fail="FAILURES", sep="; "):
    """Print ok, or fail with {n}, {names} (the failed labels joined by sep) and {fails} (the list) filled in
    (nothing if that text is empty), and exit."""
    msg = ok if not FAILS else fail.format(n=len(FAILS), names=sep.join(FAILS), fails=FAILS)
    if msg:
        print(msg)
    sys.exit(1 if FAILS else 0)
