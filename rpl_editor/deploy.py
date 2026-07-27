#!/usr/bin/env python3
"""Deploy a build over the LIVE update-partition RPL(s), with md5-verified
per-module backups.

    python deploy.py B16        # deploys every out/B16_*.rpl it finds
    python deploy.py restore    # restores every module that has a backup

A build may touch more than one module: both t6_cafef_rpl (ZM/shared engine) and
t6mp_cafef_rpl stay resident and carry DUPLICATE statically-linked engine code,
so an engine-level edit generally has to be applied to BOTH (see
wiiu_ref/rpl_sigpatch.py, which says the same about the auth check).

Refuses to write unless the live file matches its known baseline, so a build
left behind by another session is never silently clobbered.
"""
import sys, os, glob, shutil, hashlib

CODE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BAK_SUFFIX = ".pre_rpleditor.bak"

# Accumulated-patch LIVE builds as of this session -- NOT retail stock.
# Retail: t6mp .orig = c26c02b685a708e4af5c65216f14329c
# The t6mp baseline is byte-identical to .bak_pre900p, i.e. 900p is NOT in it
# (deliberate -- see the stay-720p decision).
BASELINE = {
    "t6mp_cafef_rpl.rpl": "9c55e25b174219835113f264f9e06113",
    "t6_cafef_rpl.rpl":   "b843efa9c10bbd5491ba87784630e30b",
}


def md5(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def ensure_backup(name):
    live = os.path.join(CODE, name)
    bak = live + BAK_SUFFIX
    if not os.path.exists(bak):
        cur = md5(live)
        if cur != BASELINE[name]:
            sys.exit(f"REFUSING {name}: live md5 {cur} != baseline {BASELINE[name]}\n"
                     f"  No backup exists and this is not the file we gated against.")
        shutil.copy2(live, bak)
        print(f"  backup created: {os.path.basename(bak)}")
    if md5(bak) != BASELINE[name]:
        sys.exit(f"REFUSING {name}: backup md5 != baseline {BASELINE[name]}")
    return live, bak


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    what = sys.argv[1]

    if what == "restore":
        n = 0
        for name in BASELINE:
            bak = os.path.join(CODE, name + BAK_SUFFIX)
            if os.path.exists(bak):
                shutil.copy2(bak, os.path.join(CODE, name))
                print(f"  restored {name}  md5={md5(os.path.join(CODE, name))}")
                n += 1
        print(f"restored {n} module(s)" if n else "nothing to restore")
        return

    srcs = sorted(glob.glob(os.path.join(OUT, f"{what}_*.rpl")))
    if not srcs:
        sys.exit(f"no builds matching {what}_*.rpl in {OUT}")
    for src in srcs:
        name = os.path.basename(src)[len(what) + 1:]
        if name not in BASELINE:
            sys.exit(f"unknown target module {name!r}")
        print(f"{name}:")
        live, _ = ensure_backup(name)
        want = md5(src)
        shutil.copy2(src, live)
        got = md5(live)
        print(f"  deployed {os.path.basename(src)}")
        print(f"  live md5={got}  {'VERIFIED' if got == want else 'MISMATCH!'}")
        if got != want:
            sys.exit(1)


if __name__ == "__main__":
    main()
