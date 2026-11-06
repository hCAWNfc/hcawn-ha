#!/usr/bin/env python3
"""Refreshes the engine glue from the hcawn-fc working folder. Run it before every build.

  python tools/sync_core.py --fc ../../fc_pypi

What it does:
  * copies src/hcawn_fc/_core/*.py from fc_pypi into src/hcawn_ha/_core/ (the imports are already relative there)
  * copies csrc/hcawn.cpp from fc_pypi into csrc/ (private, never committed: see .gitignore)
"""
import argparse, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fc", required=True, help="path of the fc_pypi folder")
    a = ap.parse_args()
    fc = os.path.abspath(os.path.expanduser(a.fc))
    core_src, cpp_src = os.path.join(fc, "src", "hcawn_fc", "_core"), os.path.join(fc, "csrc", "hcawn.cpp")
    for p in (core_src, cpp_src):
        if not os.path.exists(p):
            sys.exit(f"not found: {p}")
    dst = os.path.join(ROOT, "src", "hcawn_ha", "_core")
    os.makedirs(dst, exist_ok=True)
    for f in sorted(os.listdir(core_src)):
        if f.endswith(".py"):
            shutil.copy(os.path.join(core_src, f), os.path.join(dst, f))
            print("  _core/" + f)
    os.makedirs(os.path.join(ROOT, "csrc"), exist_ok=True)
    shutil.copy(cpp_src, os.path.join(ROOT, "csrc", "hcawn.cpp"))
    print("  csrc/hcawn.cpp (private)")
    print("done. Next: bash tools/build_release.sh")


if __name__ == "__main__":
    main()
