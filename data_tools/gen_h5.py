#!/usr/bin/env python3
"""HA5 data: TERSE / telegraphic phrasing like real people (and the official Home Assistant test sentences) use:
  "TV pause", "unpause TV", "bedroom lamp 50%", "brightness bedroom lamp 50%", "thermostat on", "close Sliding Door", "return rover to base",
  "next item on TV", "go back on TV", "skip the track on the TV", "Overhead light off" - plus Title Case / UPPER names ("TV", "Sliding Door").
No LLM, instant. The device phrase in the answer is always the exact text from the request.
    python gen_h5.py [N=4000]  -> ha_h5_train.jsonl ; also adds it to mix_ha.py (safe to run twice)"""
import json, os, random, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_ha as G
from convert_v2 import CSS

CSS_COLORS = sorted(CSS)
SHORT_MEDIA = ["TV", "tv", "Tv", "stereo", "Stereo", "radio", "speaker", "soundbar", "Sonos", "music player", "projector"]
SHORT_CLIMATE = ["thermostat", "Thermostat", "heating", "air conditioner", "ac", "AC", "Office Thermostat"]
SHORT_VAC = ["rover", "Rover", "roomba", "Roomba", "robot vacuum", "vacuum", "Robo"]
PAUSE = ["{d} pause", "pause {d}", "pause the {d}", "{d} paused please"]
UNPAUSE = ["{d} unpause", "unpause {d}", "{d} resume", "resume {d}", "resume the {d}", "unpause the {d}"]
NEXT = ["next item on {d}", "skip song on the {d}", "skip track on the {d}", "skip to the next song on the {d}", "skip to next track on the {d}", "skip this song on the {d}",
        "skip the track on the {d}", "skip on the {d}", "skip this on the {d}", "{d} skip this", "{d} next"]
PREV = ["go back on {d}", "previous track on {d}", "{d} go back to the last song", "{d} replay the last track", "{d} previous", "{d} play the last track",
        "go back to the last song on the {d}", "play the previous song on the {d}"]
VAC_START = ["start {d}", "{d} start", "{d} go", "run {d}"]
VAC_BACK = ["return {d} to base", "{d} return to base", "{d} dock", "send {d} back to its base", "{d} go home"]
ONOFF = [("{d} on", "HassTurnOn"), ("turn {d} on", "HassTurnOn"), ("turn on {d}", "HassTurnOn"), ("{d} off", "HassTurnOff"), ("turn {d} off", "HassTurnOff"), ("turn off {d}", "HassTurnOff")]
COVER = [("close {d}", "HassTurnOff"), ("{d} close", "HassTurnOff"), ("open {d}", "HassTurnOn"), ("{d} open", "HassTurnOn")]
BRIGHT = ["{d} {n}%", "{d} brightness {n}%", "brightness {d} {n}%", "set {d} to {n}%", "change {d} to {n}%", "turn {d} to {n}%"]
COLOR = ["{d} {c}", "turn {d} {c}", "set {d} to {c}", "change {d} to {c}", "color {d} {c}"]
ADD = ["add {i} to my {l}", "put {i} on the {l}", "add {i} to the {l}", "{l} add {i}"]


def styled(d, rnd):
    x = rnd.random()
    if x < .22: return d.title()
    if x < .30: return d.upper() if len(d) <= 4 else d
    return d


def one_call(v, rnd):
    x = rnd.random()
    def media():
        return styled(rnd.choice(SHORT_MEDIA) if rnd.random() < .45 else v.device("media"), rnd)
    if x < .26:
        d = media()
        k = rnd.choice(["pause", "unpause", "next", "prev"])
        t = {"pause": (PAUSE, "HassMediaPause"), "unpause": (UNPAUSE, "HassMediaUnpause"), "next": (NEXT, "HassMediaNext"), "prev": (PREV, "HassMediaPrevious")}[k]
        return rnd.choice(t[0]).format(d=d), {"name": t[1], "arguments": {"name": d}}
    if x < .50:
        d = styled(v.device("light"), rnd)
        if rnd.random() < .55:
            n = rnd.choice([rnd.randint(1, 100), rnd.choice(range(5, 101, 5))])
            return rnd.choice(BRIGHT).format(d=d, n=n), {"name": "HassLightSet", "arguments": {"name": d, "brightness": n}}
        c = rnd.choice(CSS_COLORS)
        return rnd.choice(COLOR).format(d=d, c=c), {"name": "HassLightSet", "arguments": {"name": d, "color": c}}
    if x < .68:
        kind = rnd.choice(["light", "light", "fan", "switch", "climate", "climate"])
        d = styled(rnd.choice(SHORT_CLIMATE) if kind == "climate" and rnd.random() < .6 else v.device(kind), rnd)
        s, t = rnd.choice(ONOFF)
        return s.format(d=d), {"name": t, "arguments": {"name": d}}
    if x < .76:
        d = styled(v.device("cover"), rnd)
        s, t = rnd.choice(COVER)
        return s.format(d=d), {"name": t, "arguments": {"name": d}}
    if x < .88:
        d = styled(rnd.choice(SHORT_VAC) if rnd.random() < .5 else v.device("vacuum"), rnd)
        if rnd.random() < .5:
            return rnd.choice(VAC_START).format(d=d), {"name": "HassVacuumStart", "arguments": {"name": d}}
        return rnd.choice(VAC_BACK).format(d=d), {"name": "HassVacuumReturnToBase", "arguments": {"name": d}}
    l = rnd.choice(v.lists); it = rnd.choice(v.items)
    return rnd.choice(ADD).format(i=it, l=l), {"name": "HassListAddItem", "arguments": {"name": l, "item": it}}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 4000
    rnd = random.Random(55)
    v = G.Vocab("train", rnd)
    v.kind["climate"] = list(v.kind["climate"])
    rows, seen = [], set()
    for _ in range(n * 4):
        if len(rows) >= n: break
        k = rnd.choices([1, 2], [88, 12])[0]
        parts, calls, devs = [], [], set()
        for _ in range(k):
            q, c = one_call(v, rnd)
            nm = c["arguments"]["name"].lower()
            if nm in devs or nm in " ".join(parts).lower(): continue
            devs.add(nm); parts.append(q); calls.append(c)
        if len(calls) != k: continue
        q = parts[0]
        for p in parts[1:]:
            q += rnd.choice([" and ", ", ", " and also ", ", then "]) + p
        if rnd.random() < .5:
            q2 = q[0].upper() + q[1:]
            if all(c['arguments']['name'] in q2 and c['arguments'].get('item', '') in q2 for c in calls): q = q2
        if q.lower() in seen: continue
        seen.add(q.lower())
        rows.append(G.make_row(q, calls, "ha_h5"))
    with open(os.path.join(HERE, "ha_h5_train.jsonl"), "w", encoding="utf-8") as fo:
        for r in rows: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} rows -> ha_h5_train.jsonl")
    for t, c in collections.Counter("+".join(sorted({x['name'] for x in r['calls']})) for r in rows).most_common(10):
        print(f"   {c:5d}  {t}")
    for r in rows[:8]: print("  ", r["query"], "=>", json.dumps(r["calls"])[:140])
    mp = os.path.join(HERE, "mix_ha.py"); m = open(mp, encoding="utf-8").read()
    if "ha_h5_train" in m: print("mix_ha.py already has ha_h5_train.jsonl")
    else:
        old = '"ha_h4_train.jsonl"]'
        assert m.count(old) == 1
        open(mp, "w", encoding="utf-8").write(m.replace(old, '"ha_h4_train.jsonl", "ha_h5_train.jsonl"]')); print("mix_ha.py patched")


if __name__ == "__main__":
    main()
