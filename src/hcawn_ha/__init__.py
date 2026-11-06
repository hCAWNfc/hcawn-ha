"""hcawn-ha: a tiny model (5.7M parameters, CPU only) that turns spoken Home Assistant commands into service calls.

    from hcawn_ha import HAPipeline
    pipe = HAPipeline("int8", registry)            # registry: your Home Assistant devices
    r = pipe.handle("turn off the kitchen light")
    r["execute"], r["confirm"], r["ask"]
"""
import os as _os

# Home Assistant tools do not use the phone-model rule layers; must be set before the engine modules are imported
_os.environ.setdefault("FC_VETO", "0")
_os.environ.setdefault("FC_FIX", "0")
_os.environ.setdefault("FC_DATES", "0")
_os.environ.setdefault("FC_GROUND", "name,item,color")

try:
    from importlib.metadata import version as _v
    __version__ = _v("hcawn-ha")
except Exception:                                    # not installed (running from a source checkout)
    __version__ = "0.0.0+dev"

from .errors import HcawnError, NativeUnavailable, WeightsNotFound
from .pipeline import HAPipeline, demo_registry
from .weights import available_models, download_weights, find_weights

__all__ = ["HAPipeline", "demo_registry", "available_models", "download_weights", "find_weights", "HcawnError", "NativeUnavailable",
           "WeightsNotFound", "__version__"]
