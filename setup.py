"""Builds a platform wheel with the compiled engine (AVX2 / SSE4.1 / generic variants); metadata lives in pyproject.toml."""
import os, sys
from setuptools import setup
from setuptools.dist import Distribution
from setuptools.command.build_py import build_py

try:
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:                                  # older setuptools
    from wheel.bdist_wheel import bdist_wheel

ROOT = os.path.dirname(os.path.abspath(__file__))
PURE = os.environ.get("HCAWN_PURE") == "1"          # wheel without the engine, works everywhere (pipeline and resolver only)


class BuildPy(build_py):
    def run(self):
        super().run()
        if PURE:
            return
        sys.path.insert(0, ROOT)
        import build_native
        built = build_native.build(os.path.join(self.build_lib, "hcawn_ha", "_lib"),
                                   require=os.environ.get("HCAWN_REQUIRE_NATIVE") == "1")
        print("hcawn-ha: engine variants:", built or "none")


class Wheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = PURE                    # contains machine code

    def get_tag(self):
        if PURE:
            return "py3", "none", "any"
        _, _, plat = super().get_tag()
        return "py3", "none", plat                   # independent of the Python version (the engine is loaded with ctypes)


class Dist(Distribution):
    def has_ext_modules(self):
        return not PURE                              # the wheel goes into platlib


setup(distclass=Dist, cmdclass={"build_py": BuildPy, "bdist_wheel": Wheel})
