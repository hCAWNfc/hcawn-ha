"""Compiles the C++ engine in several variants (AVX2 / SSE4.1 / generic). Used by setup.py; can also be run by hand:

    python build_native.py [output_dir]

At run time the package picks the fastest variant the CPU supports (hcawn_ha/_native.py).
Compilation is optional: without a compiler the package installs without the engine
(set HCAWN_REQUIRE_NATIVE=1 to turn that into an error).
"""
import os, platform, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "csrc", "hcawn.cpp")
COMMON = ["-O3", "-std=c++17", "-ffast-math", "-fopenmp-simd", "-pthread", "-fPIC", "-shared", "-fvisibility=default"]


def is_x86():
    return platform.machine().lower() in ("x86_64", "amd64", "i386", "i686")


def variants():
    if is_x86():
        return [("avx2", ["-mavx2", "-mfma", "-mtune=generic"]), ("sse41", ["-msse4.1", "-mtune=generic"]), ("generic", [])]
    return [("generic", [])]


def lib_name(isa):
    return f"libhcawn_{isa}" + (".dylib" if sys.platform == "darwin" else ".so")


def compiler():
    return os.environ.get("CXX") or shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


def build(outdir, require=False):
    os.makedirs(outdir, exist_ok=True)
    cxx = compiler()
    if sys.platform.startswith("win"):
        msg = "Windows: the engine is not built yet"
    elif not cxx:
        msg = "no C++ compiler found"
    elif not os.path.isfile(SRC):
        msg = "csrc/hcawn.cpp is missing (run tools/sync_core.py)"
    else:
        msg = ""
    if msg:
        if require:
            raise RuntimeError(msg)
        print("WARNING:", msg, file=sys.stderr)
        return []
    built = []
    for isa, flags in variants():
        out = os.path.join(outdir, lib_name(isa))
        cmd = [cxx, *COMMON, *flags, "-o", out, SRC]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if require and isa == "generic":
                raise RuntimeError("compilation failed:\n" + r.stderr[-2000:])
            print(f"WARNING: variant {isa} was not built: {r.stderr[-400:]}", file=sys.stderr)
            continue
        built.append(isa)
    if require and not built:
        raise RuntimeError("no engine variant could be built")
    return built


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "src", "hcawn_ha", "_lib")
    print("built:", build(out, require=True))
