#!/usr/bin/env python3
"""hcawn-ha pipeline: one sentence in -> Home Assistant service calls (or a question) out.
    hcawn-ha chat [int8|int4|path.bin] [registry.json] [--gate 0.7]      # interactive; no registry.json = small demo house
    hcawn-ha selftest                                                    # logic tests without the model
Steps: split into commands -> model (per command, with confidence) -> direction guard -> resolver (registry) -> services.
Result of handle():
  {"execute": [service calls to run now], "confirm": [service calls that need a spoken 'yes' first],
   "ask": "question for the user or ''", "understood": [model calls], "reason": "why asked, if asked"}
Nothing is executed when the model is unsure (confidence < gate), when the verb contradicts the tool, when a color/number is invalid,
or when the device is ambiguous. Locks / alarms are never converted to a service in v1."""
import json, re, sys, time
from .resolver import resolve_calls

SEP = re.compile(r"(?<=[.!?])\s+|\s*;\s*|\s*,\s*(?:and\s+|then\s+|also\s+|plus\s+)?|\s+(?:and\s+then|and\s+also|and|then|also|plus)\s+", re.I)
ACT = re.compile(r"\b(?:turn|switch|set|make|change|dim|brighten|flip|toggle|start|stop|pause|resume|unpause|skip|play|next|previous|add|put|send|return|dock|open|close|kill|power|shut|lower|raise|get|run|begin|on|off|up|down|clean|vacuum|mop|bring|keep|hold|freeze|halt|enable|disable|activate|deactivate)\b", re.I)
REF = re.compile(r"\b(?:it|them|they|that|those|these|this|both|same)\b", re.I)

# direction guard: words that clearly say on/off or pause/next/previous must not contradict the tool the model chose
W_ON = re.compile(r"\b(?:turn(?:ed)? on|switch(?:ed)? on|power(?:ed)? up|fire up|light up|illuminate|uncover|open|raise|lift|start|activate|enable)\b|\bon\b(?! or off)", re.I)
W_OFF = re.compile(r"\b(?:lights?|lamps?) out\b|\b(?:turn(?:ed)? off|switch(?:ed)? off|power(?:ed)? down|shut|extinguish|darken|kill|douse|close|lower|deactivate|disable)\b|\boff\b(?! or on)", re.I)
W_PAUSE = re.compile(r"\b(?:pause|stop|hold|halt|freeze)\b", re.I)
W_NEXT = re.compile(r"\b(?:next|skip|forward)\b", re.I)
W_PREV = re.compile(r"\b(?:previous|prior|back|rewind|last song|replay)\b", re.I)


def split_clauses(q):
    parts = [p.strip(" .,!?") for p in SEP.split(q) if p and p.strip(" .,!?")]
    if len(parts) < 2 or REF.search(q):
        return [q]
    merged = []
    for p in parts:
        if merged and merged[-1][1] == "pending":
            merged[-1] = (merged[-1][0] + " " + p, "ok" if ACT.search(p) else "pending")
        else:
            merged.append((p, "ok" if ACT.search(p) else "pending"))
    if any(st == "pending" for _, st in merged):
        if len(merged) > 1 and merged[0][1] == "pending" and all(st == "ok" for _, st in merged[1:]):
            merged = [(merged[0][0] + " " + merged[1][0], "ok")] + merged[2:]
        else:
            return [q]
    return [p for p, _ in merged] if len(merged) >= 2 else [q]


def direction_conflict(text, call):
    """-> reason string if the words in `text` contradict the tool, else None."""
    t = call["name"]
    if t == "HassTurnOff" and W_ON.search(text) and not W_OFF.search(text):
        return "the sentence says on/open, the model chose off/close"
    if t == "HassTurnOn" and W_OFF.search(text) and not W_ON.search(text):
        return "the sentence says off/close, the model chose on/open"
    if t in ("HassMediaNext", "HassMediaPrevious") and W_PAUSE.search(text) and not (W_NEXT.search(text) or W_PREV.search(text)):
        return "the sentence says pause/stop, the model chose next/previous"
    if t in ("HassTurnOn", "HassTurnOff") and re.search(r"\b(?:stop|halt|freeze)\b", text, re.I) and re.search(r"\b(?:door|gate|blinds?|shutters?|curtains?|shade|awning|garage)s?\b", text, re.I):
        return "stop/halt on a door or blind: there is no stop action, closing it would be wrong"
    if t == "HassTurnOn" and re.search(r"\bunlock(?:ed|ing)?\b", text, re.I):
        return "the sentence says unlock, the model chose on (= lock)"
    if t == "HassTurnOff" and re.search(r"(?<!un)\block(?:ed|ing)?\b", text, re.I) and not re.search(r"\bunlock", text, re.I):
        return "the sentence says lock, the model chose off (= unlock)"
    if t == "HassMediaNext" and W_PREV.search(text) and not W_NEXT.search(text):
        return "the sentence says previous, the model chose next"
    if t == "HassMediaPrevious" and W_NEXT.search(text) and not W_PREV.search(text):
        return "the sentence says next, the model chose previous"
    return None


class HAPipeline:
    def __init__(self, model="int8", registry=None, gate=0.7, generate=None, threads=1):
        """model: "int8", "int4" or a path to a .bin file. registry: list of devices
        {"entity_id","name","area","aliases","device_class"}. generate: replaces the model (tests)."""
        self.registry, self.gate = registry if registry is not None else demo_registry(), gate
        if generate is not None:
            self._generate = generate
            return
        from ._engine import make_generate
        self._generate = make_generate(model, threads=threads)

    def handle(self, text):
        from ._core.fc_common import parse_calls
        t0 = time.perf_counter()
        res = {"execute": [], "confirm": [], "ask": "", "understood": [], "reason": "", "ms": 0, "trace": [], "resolved": [], "min_conf": None}
        calls, scores = [], []
        for part in split_clauses(text.strip()):
            tg = time.perf_counter()
            out, sc = self._generate(part)
            if not isinstance(sc, dict):
                sc = {"min_vt": sc}
            res["trace"].append({"clause": part, "raw": out, "scores": {k: round(float(v), 4) for k, v in sc.items()}, "ms": int(1000 * (time.perf_counter() - tg))})
            p = parse_calls(out)
            if p is None:
                return self._done(res, t0, ask="Sorry, I did not understand that. Could you say it again?", reason="model output not valid")
            for c in p:
                why = direction_conflict(part, c)
                if why:
                    res["understood"] = calls + p
                    return self._done(res, t0, ask=f"I'm not sure I understood \"{part}\". Could you rephrase it?", reason=why)
            calls += p; scores.append(sc["min_vt"])
        res["understood"] = calls
        res["min_conf"] = round(min(scores), 4) if scores else None
        if not calls:
            return self._done(res, t0, reason="no command found")
        if min(scores) < self.gate:
            return self._done(res, t0, ask="I'm not sure I understood that. Could you say it a different way?", reason=f"confidence {min(scores):.2f} < {self.gate}")
        r = resolve_calls(calls, self.registry)
        asks = []
        for a in r["actions"]:
            res["resolved"].append({"phrase": a["phrase"], "intent": a["intent"], "status": a["status"], "entity_ids": a["entity_ids"], "confirm": a["confirm"],
                                    "candidates": [(c["name"], c["score"]) for c in a["candidates"][:3]]})
            if a["status"] == "ok" and a["service"]:
                (res["confirm"] if a["confirm"] else res["execute"]).append(a["service"])
            elif a["status"] == "ok":
                asks.append(f"I can't control {a['phrase']} yet.")
        if r["message"]:
            asks.append(r["message"])
        if asks:
            res["ask"] = " ".join(asks)
            res["reason"] = "resolver"
        return self._done(res, t0)

    @staticmethod
    def _done(res, t0, ask=None, reason=None):
        if ask:
            res["ask"] = ask
        if reason:
            res["reason"] = reason
        res["ms"] = int(1000 * (time.perf_counter() - t0))
        return res


DEMO = [("light.kitchen", "kitchen light"), ("light.living_ceiling", "living room ceiling light"), ("light.living_lamp", "living room floor lamp"),
        ("light.bedroom", "bedroom light"), ("fan.bedroom", "bedroom fan"), ("cover.living", "living room blinds"), ("cover.garage", "garage door"),
        ("media_player.living", "living room speaker"), ("vacuum.robot", "robot vacuum"), ("todo.shopping", "shopping list"), ("climate.house", "thermostat")]


def demo_registry():
    return [{"entity_id": e, "name": n, "area": None, "aliases": [], "device_class": "garage" if "garage" in e else None} for e, n in DEMO]


def selftest():
    outs = {
        "turn on the kitchen light": ('[{"name":"HassTurnOn","arguments":{"name":"kitchen light"}}]', 0.99),
        "turn on the kitchen light and set the bedroom light to 40": ('[{"name":"HassTurnOn","arguments":{"name":"kitchen light"}}]', 0.99),
        "set the bedroom light to 40": ('[{"name":"HassLightSet","arguments":{"name":"bedroom light","brightness":40}}]', 0.99),
        "stop the living room speaker": ('[{"name":"HassMediaPrevious","arguments":{"name":"living room speaker"}}]', 0.99),
        "illuminate the bedroom light": ('[{"name":"HassTurnOff","arguments":{"name":"bedroom light"}}]', 0.99),
        "open the garage door": ('[{"name":"HassTurnOn","arguments":{"name":"garage door"}}]', 0.99),
        "dim the light": ('[{"name":"HassLightSet","arguments":{"name":"light","brightness":30}}]', 0.99),
        "turn the kitchen light blue": ('[{"name":"HassLightSet","arguments":{"name":"kitchen light","color":"ing"}}]', 0.99),
        "kitchen thing": ('[{"name":"HassTurnOn","arguments":{"name":"kitchen light"}}]', 0.4),
    }
    p = HAPipeline(None, demo_registry(), 0.7, generate=lambda t: outs[t])
    ok = True
    def chk(q, cond, label):
        nonlocal ok
        r = p.handle(q); good = bool(cond(r)); ok = ok and good
        print(("ok   " if good else "FAIL ") + label, "|", json.dumps({k: r[k] for k in ("execute", "confirm", "ask", "reason")})[:230])
    chk("turn on the kitchen light", lambda r: len(r["execute"]) == 1 and not r["ask"], "simple command executes")
    chk("set the bedroom light to 40", lambda r: r["execute"][0]["data"].get("brightness_pct") == 40, "brightness value")
    chk("stop the living room speaker", lambda r: not r["execute"] and r["ask"], "stop -> Previous is held")
    chk("illuminate the bedroom light", lambda r: not r["execute"] and r["ask"], "illuminate -> TurnOff is held")
    chk("open the garage door", lambda r: not r["execute"] and len(r["confirm"]) == 1, "garage door needs confirmation")
    chk("dim the light", lambda r: not r["execute"] and r["ask"], "ambiguous device is asked")
    chk("turn the kitchen light blue", lambda r: not r["execute"] and r["ask"], "invalid color is asked")
    chk("kitchen thing", lambda r: not r["execute"] and "confidence" in r["reason"], "low confidence is asked")
    print("\nALL OK" if ok else "\nSOME FAILED")
    return ok


def chat(A):
    gate = float(A[A.index("--gate") + 1]) if "--gate" in A else 0.7
    pos = [a for i, a in enumerate(A) if not a.startswith("--") and not (i > 0 and A[i - 1] == "--gate")]
    reg = json.load(open(pos[1])) if len(pos) > 1 else demo_registry()
    pipe = HAPipeline(pos[0] if pos else "int8", reg, gate)
    print(f"{len(reg)} devices, gate {gate}. Type a sentence (empty line = quit). Risky actions wait for yes / no.")
    pending = []
    while True:
        try:
            q = input("> ").strip()
        except EOFError:
            break
        if not q:
            break
        if pending and q.lower() in ("yes", "y", "da", "yes please", "do it", "confirm"):
            for s in pending: print("  RUN     ", json.dumps(s))
            pending = []
            continue
        if pending and q.lower() in ("no", "n", "ne", "cancel", "stop"):
            print("  cancelled"); pending = []
            continue
        pending = []
        r = pipe.handle(q)
        pending = list(r["confirm"])
        for t in r["trace"]:
            sc = t["scores"]
            print(f"  [model] \"{t['clause']}\" -> {t['raw']}")
            print("          confidence: " + "  ".join(f"{k}={sc[k]}" for k in ("min_vt", "tool_p", "val_min", "min_res", "min_full", "mean_lp") if k in sc) + f"  | {t['ms']} ms")
        if r["min_conf"] is not None:
            print(f"  [gate]  min_vt {r['min_conf']} {'>=' if r['min_conf'] >= gate else '<'} {gate} -> {'pass' if r['min_conf'] >= gate else 'ASK'}")
        for a in r["resolved"]:
            print(f"  [match] {a['intent']} \"{a['phrase']}\" -> {a['status']} {a['entity_ids']} candidates {a['candidates']}" + (" CONFIRM" if a["confirm"] else ""))
        for s in r["execute"]: print("  RUN     ", json.dumps(s))
        for s in r["confirm"]: print("  CONFIRM ", json.dumps(s), " (waiting: yes / no)")
        if r["ask"]: print("  ASK     ", r["ask"], f" [{r['reason']}]")
        print(f"  total {r['ms']} ms\n")

