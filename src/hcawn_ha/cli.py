"""Command line: hcawn-ha download | say | chat | demo | selftest | info"""
import argparse
import json
import sys

from . import __version__
from .errors import HcawnError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hcawn-ha", description="Tiny Home Assistant command model (CPU only).")
    ap.add_argument("--version", action="version", version=f"hcawn-ha {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the weights from Hugging Face")
    d.add_argument("model", nargs="?", default="int8", choices=["int8", "int4", "all"])
    s = sub.add_parser("say", help="run one sentence against the demo house and print the service calls")
    s.add_argument("text"); s.add_argument("--model", default="int8"); s.add_argument("--gate", type=float, default=0.7)
    c = sub.add_parser("chat", help="interactive prompt (args: [model] [registry.json] [--gate 0.7])")
    c.add_argument("rest", nargs=argparse.REMAINDER)
    m = sub.add_parser("demo", help="open the simulated house in your browser (args: [model] [--port 8765] [--gate 0.7])")
    m.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("selftest", help="logic tests without the model")
    sub.add_parser("info", help="engine variant and available weights")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "download":
            from .weights import download_weights
            download_weights(a.model)
        elif a.cmd == "say":
            from .pipeline import HAPipeline
            r = HAPipeline(a.model, None, a.gate).handle(a.text)
            print(json.dumps({k: r[k] for k in ("execute", "confirm", "ask", "reason", "understood", "min_conf", "ms")}, indent=1, ensure_ascii=False))
        elif a.cmd == "chat":
            from .pipeline import chat
            chat(a.rest)
        elif a.cmd == "demo":
            from .demo.app import main as demo
            demo(a.rest)
        elif a.cmd == "selftest":
            from .pipeline import selftest
            sys.exit(0 if selftest() else 1)
        elif a.cmd == "info":
            from . import _native
            from .weights import available_models, cache_dir
            print(f"hcawn-ha {__version__}\nengine variants built: {_native.available() or 'none (this is the pure-Python wheel)'}\nengine in use: {_native.best_isa()}\nweights: {available_models() or 'none'} (cache: {cache_dir()})")
    except HcawnError as e:
        sys.exit(f"hcawn-ha: {e}")


if __name__ == "__main__":
    main()
