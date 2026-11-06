"""Glue to the public hcawn-fc engine (pip install hcawn-fc). Sets the environment the Home Assistant tools need and makes the engine's
helper modules importable under their short names (fc_common, fc_conf, hcawn_cpp). Import this before anything else."""
import os, sys

# the rule layers of the phone model are off for Home Assistant tools; must be set before the engine modules are imported
os.environ.setdefault("FC_VETO", "0")
os.environ.setdefault("FC_FIX", "0")
os.environ.setdefault("FC_DATES", "0")
os.environ.setdefault("FC_GROUND", "name,item,color")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from hcawn_fc import _native
    from hcawn_fc._core import fc_common, fc_conf, hcawn_cpp
except ImportError as e:                       # pragma: no cover
    raise SystemExit("hcawn-fc is required for the engine and the prompt format:  pip install hcawn-fc   (" + str(e) + ")")
for _n, _m in (("fc_common", fc_common), ("fc_conf", fc_conf), ("hcawn_cpp", hcawn_cpp)):
    sys.modules.setdefault(_n, _m)

import json
HA_TOOLS = json.load(open(os.path.join(HERE, "ha_tools.json"), encoding="utf-8"))


def make_generate(bin_path, threads=4):
    """Load a hcawn_ha weights file (int8.bin / int4.bin from huggingface.co/datatab/hcawn-ha) and return generate(text) -> (json, scores)."""
    lib = _native.lib_path()
    if lib is None:
        raise RuntimeError("the local engine is not available on this platform (Linux x86-64 only for now)")
    cm = hcawn_cpp.CppModel(bin_path, lib_path=lib, threads=threads)
    cache = hcawn_cpp.PrefixCache(cm)

    def gen(text):
        ex = {"tools": HA_TOOLS, "context": "", "query": text}
        c = {}
        out = hcawn_cpp.cpp_generate(cm, hcawn_cpp.encode_prompt(fc_common.render_prompt(ex)), HA_TOOLS, 500, cache, query=text, context="", conf=c)
        sc = fc_conf.scores(c.get("steps", []))
        sc["forced_free"] = len(c.get("steps", []))
        return out, sc
    return gen
