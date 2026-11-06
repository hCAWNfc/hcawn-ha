"""Loads a weights file with the compiled engine and returns the `generate(text) -> (json_text, scores)` function the pipeline uses."""
import json
import os
import threading

from . import _native
from .errors import NativeUnavailable, WeightsNotFound
from .weights import find_weights

_HERE = os.path.dirname(os.path.abspath(__file__))


def tools():
    p = os.path.join(_HERE, "data", "tools.json")
    if not os.path.isfile(p):
        raise WeightsNotFound(f"tool definitions not found: {p}")
    return json.load(open(p, encoding="utf-8"))["tools"]


def make_generate(model="int8", threads=1):
    lib = _native.lib_path()
    if lib is None:
        raise NativeUnavailable("the local engine is not available in this install (it ships for Linux x86-64).")
    from ._core import fc_common, fc_conf
    from ._core.hcawn_cpp import CppModel, PrefixCache, cpp_generate, encode_prompt
    path, tl = find_weights(model), tools()
    cm = CppModel(path, lib_path=lib, threads=threads)
    cache, lock = PrefixCache(cm), threading.Lock()          # the engine is not thread-safe: calls are serialized

    def gen(text):
        ex = {"tools": tl, "context": "", "query": text}
        c = {}
        with lock:
            out = cpp_generate(cm, encode_prompt(fc_common.render_prompt(ex)), tl, 500, cache, query=text, context="", conf=c)
        sc = fc_conf.scores(c.get("steps", []))
        sc["forced_free"] = len(c.get("steps", []))          # number of positions where the model really chose a byte
        return out, sc
    gen.weights, gen.isa = path, _native.best_isa()
    return gen
