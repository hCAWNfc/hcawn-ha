#!/usr/bin/env python3
"""hcawn_ha local demo: a simulated house you control by typing. Everything runs on this computer (CPU, no network).
    python app.py [int8.bin] [--port 8765] [--gate 0.7]
Open http://127.0.0.1:8765 (it opens by itself)."""
import copy, json, os, re, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hcawn_ha")))
import house

LOCK = threading.Lock()
S = {"model": None, "pipe": None, "state": house.initial_state(), "pending": [], "gate": 0.7, "paths": {}, "pipes": {}}


# things this model cannot do yet: say so plainly instead of failing quietly
UNSUPPORTED = [
    ("fan speed", r"\bfan\b.*\b(speed|faster|slower|medium|high|low|\d+\s?(%|percent))|\b(speed up|slow down)\b"),
    ("scenes and modes", r"\b(scene|movie mode|party mode|night mode|sleep mode|mode)\b"),
    ("timers and schedules", r"\b(timer|alarm|remind|schedule)\b|\b(in|for|after)\s+\d+\s*(sec|second|min|minute|hour)s?\b|\bat\s+\d{1,2}(:\d\d)?\s*(am|pm)\b"),
    ("volume control", r"\b(volume|louder|quieter|mute|unmute)\b|turn (it )?(up|down) the (music|volume|speaker|stereo|tv|sound)|turn the (music|volume|sound|tv|stereo|speaker) (up|down)"),
    ("setting a blind position", r"\b(blinds?|shutters?|curtains?|garage)\b.*(\d+\s?(%|percent)|half|halfway)"),
    ("questions about device state", r"^\s*(is|are|what|what's|whats|which|who|when|how (is|are|many|much|warm|cold|hot|long))\b"),
]


def _unsupported(text):
    low = text.lower()
    for label, pat in UNSUPPORTED:
        if re.search(pat, low):
            return label
    return None


def say(text):
    """One sentence in -> pipeline -> run what is safe, hold what needs a 'yes'."""
    with LOCK:
        low = text.strip().lower()
        if S["pending"] and low in ("yes", "y", "yes please", "do it", "confirm"):
            return confirm(True)
        if S["pending"] and low in ("no", "n", "cancel", "stop", "no thanks"):
            return confirm(False)
        S["pending"] = []
        pipe = S["pipe"]
        r = pipe.handle(text)
        r["tentative"] = False
        lab = _unsupported(text)
        if lab:
            # the model still produced something (shown in the console), but we do not run a guess for a request it cannot do
            r.update(execute=[], confirm=[], ask=f"This model does not support {lab} yet.", reason="not supported yet", unsupported=lab)
        elif r["reason"].startswith("confidence"):
            # the model is unsure: do not run anything, but show the best guess and let the person say "do it anyway"
            g, pipe.gate = pipe.gate, -1.0
            try: r2 = pipe.handle(text)
            finally: pipe.gate = g
            guess = r2["execute"] + r2["confirm"]
            if guess and not r2["ask"]:
                r2.update(tentative=True, confirm=guess, execute=[], reason=r["reason"], ask="")
                r = r2
        before, changed = copy.deepcopy(S["state"]), []
        for svc in r["execute"]:
            changed += house.apply(S["state"], svc)
        S["pending"] = list(r["confirm"])
        return {"result": r, "changed": changed, "events": house.events(before, S["state"], changed), "state": S["state"], "pending": S["pending"], "answer": None}


def confirm(ok):
    changed = []
    done = list(S["pending"])
    before = copy.deepcopy(S["state"])
    if ok:
        for svc in done:
            changed += house.apply(S["state"], svc)
    S["pending"] = []
    return {"result": None, "changed": changed, "events": house.events(before, S["state"], changed), "state": S["state"], "pending": [], "answer": "yes" if ok else "no", "ran": done if ok else []}


def _variant(path):
    n = os.path.basename(path).lower()
    return "int4" if "int4" in n else "int8" if "int8" in n else os.path.basename(path)


def models_info():
    return [{"name": k, "file": os.path.basename(p), "mb": round(os.path.getsize(p) / 1e6, 1)} for k, p in S["paths"].items()]


def get_pipe(name):
    if name not in S["pipes"]:
        from ha_pipeline import HAPipeline
        print(f"loading {S['paths'][name]} ...", flush=True)
        S["pipes"][name] = HAPipeline(S["paths"][name], house.registry(), S["gate"])
    return S["pipes"][name]


def switch_model(name):
    """Swap the active model (loaded on first use, then kept). The house state stays; a pending question is dropped."""
    if name not in S["paths"]:
        return {"error": f"unknown model {name}"}
    S["pipe"], S["model"], S["pending"] = get_pipe(name), name, []
    return {"model": name, "models": models_info()}


def compare(text):
    """Run the same sentence through every available model. Nothing is executed and the house does not change."""
    out = []
    for name in S["paths"]:
        pipe = get_pipe(name)
        t = time.time()
        r = pipe.handle(text)
        out.append({"model": name, "result": r, "wall_ms": round((time.time() - t) * 1000)})
    sig = {json.dumps([o["result"]["execute"], o["result"]["confirm"]], sort_keys=True) for o in out}
    return {"text": text, "results": out, "same": len(sig) == 1}


def _sync_registry():
    reg = house.registry()
    for p in S["pipes"].values():
        p.registry = reg


def add_device(body):
    try:
        d = house.add_device(S["state"], body.get("name"), body.get("area"), body.get("kind"))
    except ValueError as e:
        return 400, {"error": str(e)}
    _sync_registry()
    return 200, {"device": d, "devices": house.registry(), "state": S["state"]}


def remove_device(body):
    eid = str(body.get("entity_id", ""))
    if eid not in {x[0] for x in house.EXTRAS}:
        return 400, {"error": "That device cannot be removed."}
    house.remove_device(S["state"], eid)
    S["pending"] = []
    _sync_registry()
    return 200, {"devices": house.registry(), "state": S["state"]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif self.path == "/api/house":
            self._send(200, {"devices": house.registry(), "state": S["state"], "gate": S["gate"], "model": S["model"], "models": models_info()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/say":
                self._send(200, say(str(body.get("text", ""))[:300]))
            elif self.path == "/api/confirm":
                with LOCK: self._send(200, confirm(bool(body.get("ok"))))
            elif self.path == "/api/model":
                with LOCK: self._send(200, switch_model(str(body.get("name", ""))))
            elif self.path == "/api/compare":
                with LOCK: self._send(200, compare(str(body.get("text", ""))[:300]))
            elif self.path == "/api/device":
                with LOCK: code, res = add_device(body)
                self._send(code, res)
            elif self.path == "/api/device/remove":
                with LOCK: code, res = remove_device(body)
                self._send(code, res)
            elif self.path == "/api/reset":
                with LOCK:
                    S["state"] = house.initial_state(); S["pending"] = []; _sync_registry()
                self._send(200, {"state": S["state"], "devices": house.registry()})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:                                   # keep the demo alive; the page shows the message
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def serve(pipe, port, gate=0.7, open_browser=True, model=None, paths=None):
    S["pipe"], S["gate"], S["model"] = pipe, gate, model
    S["paths"], S["pipes"] = paths or {}, {model: pipe}
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}"
    print(f"hcawn_ha demo running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    A = sys.argv[1:]
    port = int(A[A.index("--port") + 1]) if "--port" in A else 8765
    gate = float(A[A.index("--gate") + 1]) if "--gate" in A else 0.7
    pos = [a for i, a in enumerate(A) if not a.startswith("--") and not (i > 0 and A[i - 1] in ("--port", "--gate"))]
    binp = pos[0] if pos else os.path.join(HERE, "int8.bin")
    if not os.path.exists(binp):
        sys.exit(f"Model not found: {binp}\nDownload it first:  hf download datatab/hcawn-ha int8.bin int4.bin --local-dir demo\nor pass a path:  python app.py path/to/int8.bin")
    from ha_pipeline import HAPipeline
    print(f"loading {binp} ...", flush=True)
    cur = _variant(binp)
    paths = {cur: binp}
    for v in ("int8", "int4"):                       # offer the sibling quantization if it sits next to the chosen file
        sib = os.path.join(os.path.dirname(binp), f"{v}.bin")
        if v not in paths and os.path.exists(sib):
            paths[v] = sib
    paths = dict(sorted(paths.items(), reverse=True))  # int8 first
    serve(HAPipeline(binp, house.registry(), gate), port, gate, model=cur, paths=paths)


if __name__ == "__main__":
    main()
