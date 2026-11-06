#!/usr/bin/env python3
"""HA4 data: template-based (no LLM, instant) examples for gaps found on the public V2 test split and in our own errors:
  - toggle said as "switch the X state / change the status of X / switch X on or off"
  - rare on/off/open/close verbs (illuminate, light up, uncover, extinguish, darken, ...)
  - "turn/make the X <color>" incl. every CSS color name -> HassLightSet color
  - "stop/halt/hold the X" on media -> HassMediaPause
  - 25% of the rows chain 2-3 such commands, every command keeps its own value
    python gen_h4.py [N=4000]   -> ha_h4_train.jsonl ; also adds it to mix_ha.py (safe to run twice)
Devices come from the same training vocabulary as gen_ha.py (the held-out 1/5 stays for eval)."""
import json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_ha as G
from convert_v2 import CSS

CSS_COLORS = sorted(CSS)
TOGGLE = ["switch the {d} state", "switch the state of the {d}", "change the state of the {d}", "change the status of the {d}", "flip the state of the {d}",
          "toggle the state of the {d}", "can you change the state of {d}?", "switch the {d} on or off", "switch {d} on or off", "alternate the {d} state"]
LIGHT_ON = ["illuminate the {d}", "light up the {d}", "could you please illuminate the {d}?", "brighten up the {d}", "fire up the {d}", "put the {d} on", "get the {d} going"]
OFF = ["extinguish the {d}", "darken the {d}", "kill the {d}", "put out the {d}", "shut off the {d}", "douse the {d}", "cut the {d}"]
COVER_OPEN = ["uncover the {d}", "raise the {d}", "lift the {d}", "open up the {d}", "draw the {d} open"]
COVER_CLOSE = ["lower the {d}", "shut the {d}", "draw the {d} closed", "close up the {d}", "pull the {d} down"]
COLOR_T = ["turn the {d} {c}", "make the {d} {c}", "set the {d} to {c}", "switch the {d} to {c}", "{d} {c} please", "change the {d} to {c}", "turn {d} {c}", "paint the {d} {c}"]
PAUSE_T = ["stop the {d}", "halt the {d}", "hold the {d}", "stop the music on the {d}", "pause the {d}", "stop the music playing on the {d}"]


def one_call(v, rnd):
    x = rnd.random()
    if x < .30:
        kind = rnd.choices(["light", "fan", "switch", "cover"], [5, 3, 2, 3])[0]
        d = v.device(kind)
        return rnd.choice(TOGGLE).format(d=d), {"name": "HassToggle", "arguments": {"name": d}}
    if x < .45:
        d = v.device("light")
        return rnd.choice(LIGHT_ON).format(d=d), {"name": "HassTurnOn", "arguments": {"name": d}}
    if x < .58:
        kind = rnd.choice(["light", "light", "fan", "switch"])
        d = v.device(kind)
        return rnd.choice(OFF).format(d=d), {"name": "HassTurnOff", "arguments": {"name": d}}
    if x < .66:
        d = v.device("cover")
        return rnd.choice(COVER_OPEN).format(d=d), {"name": "HassTurnOn", "arguments": {"name": d}}
    if x < .72:
        d = v.device("cover")
        return rnd.choice(COVER_CLOSE).format(d=d), {"name": "HassTurnOff", "arguments": {"name": d}}
    if x < .90:
        d = v.device("light")
        c = rnd.choice(CSS_COLORS)
        return rnd.choice(COLOR_T).format(d=d, c=c), {"name": "HassLightSet", "arguments": {"name": d, "color": c}}
    d = v.device("media")
    return rnd.choice(PAUSE_T).format(d=d), {"name": "HassMediaPause", "arguments": {"name": d}}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 4000
    rnd = random.Random(44)
    v = G.Vocab("train", rnd)
    v.kind["light"] = list(v.kind["light"]) + ["lighting", "lights"]
    rows, seen = [], set()
    for _ in range(n * 3):
        if len(rows) >= n:
            break
        k = rnd.choices([1, 2, 3], [75, 17, 8])[0]
        parts, calls, devs = [], [], set()
        for _ in range(k):
            q, c = one_call(v, rnd)
            if c["arguments"]["name"].lower() in devs or c["arguments"]["name"].lower() in " ".join(parts).lower():
                continue
            devs.add(c["arguments"]["name"].lower()); parts.append(q); calls.append(c)
        if len(calls) != k:
            continue
        parts = [p.rstrip('?.') if i < len(parts) - 1 else p for i, p in enumerate(parts)]
        q = parts[0]
        for p in parts[1:]:
            q += rnd.choice([" and ", ", ", " and also ", ", then ", " and "]) + p
        if rnd.random() < .35:
            q = q.lower()
        elif rnd.random() < .7:
            q = q[0].upper() + q[1:]
        if rnd.random() < .5 and not q.endswith("?"):
            q = q.rstrip(".") + "."
        if rnd.random() < .12:
            q = rnd.choice(["please ", "hey ", "can you ", "could you "]) + q[0].lower() + q[1:]
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        rows.append(G.make_row(q, calls, "ha_h4"))
    with open(os.path.join(HERE, "ha_h4_train.jsonl"), "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    import collections
    print(f"{len(rows)} rows -> ha_h4_train.jsonl")
    for t, c in collections.Counter("+".join(sorted({x['name'] for x in r['calls']})) for r in rows).most_common(8):
        print(f"   {c:5d}  {t}")
    for r in rows[:6]:
        print("  ", r["query"], "=>", json.dumps(r["calls"])[:150])
    mp = os.path.join(HERE, "mix_ha.py")
    m = open(mp, encoding="utf-8").read()
    if "ha_h4_train" in m:
        print("mix_ha.py already has ha_h4_train.jsonl")
    else:
        old = '"ha_h3_train.jsonl"]'
        assert m.count(old) == 1
        open(mp, "w", encoding="utf-8").write(m.replace(old, '"ha_h3_train.jsonl", "ha_h4_train.jsonl"]'))
        print("mix_ha.py patched")


if __name__ == "__main__":
    main()
