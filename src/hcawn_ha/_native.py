"""Picks the compiled engine variant the CPU supports (AVX2 > SSE4.1 > generic)."""
import os, platform, subprocess, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(_HERE, "_lib")
ORDER = ["avx2", "sse41", "generic"]
_EXT = ".dylib" if sys.platform == "darwin" else ".so"


def _is_x86():
    return platform.machine().lower() in ("x86_64", "amd64", "i386", "i686")


def _cpu_flags():
    """Lower-case set of CPU feature flags (x86). Empty set if they cannot be read."""
    flags = set()
    try:
        if sys.platform.startswith("linux"):
            for line in open("/proc/cpuinfo", encoding="utf-8", errors="ignore"):
                if line.lower().startswith("flags"):
                    flags |= set(line.split(":", 1)[1].lower().split())
                    break
        elif sys.platform == "darwin":
            for key in ("machdep.cpu.features", "machdep.cpu.leaf7_features"):
                out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout
                flags |= set(out.lower().replace(".", "_").split())
    except Exception:
        pass
    return flags


def available():
    return [i for i in ORDER if os.path.isfile(os.path.join(LIB_DIR, f"libhcawn_{i}{_EXT}"))]


def best_isa():
    """Fastest variant that is both built and supported by the CPU. HCAWN_HA_ISA=avx2|sse41|generic forces one."""
    have = available()
    forced = os.environ.get("HCAWN_HA_ISA", "").strip().lower()
    if forced:
        if forced not in ORDER:
            raise RuntimeError(f"HCAWN_HA_ISA must be one of {ORDER}")
        if forced not in have:
            raise RuntimeError(f"variant '{forced}' is not built in this package (available: {have or 'none'})")
        return forced
    if not have:
        return None
    ok = ["generic"]
    if _is_x86():
        f = _cpu_flags()
        if "sse4_1" in f: ok.insert(0, "sse41")
        if {"avx2", "fma"} <= f: ok.insert(0, "avx2")
    for isa in ORDER:
        if isa in have and isa in ok:
            return isa
    return None


def lib_path(isa=None):
    isa = isa or best_isa()
    if isa is None:
        return None
    return os.path.join(LIB_DIR, f"libhcawn_{isa}{_EXT}")
