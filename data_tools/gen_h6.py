#!/usr/bin/env python3
"""HA6 data (use together with gen_h5, ONE training run): locks, garage / gates, and whole-room targets.
  - locks: "lock the front door" -> HassTurnOn, "unlock the front door" -> HassTurnOff   (this is what Home Assistant itself does:
    intent/__init__.py: on = lock, off = unlock; official tests "lock front door" = HassTurnOn). The pipeline always asks for a spoken yes first.
  - garage doors / gates: open/close/raise/lower -> HassTurnOn / HassTurnOff
  - rooms: "all the lights in the kitchen", "kitchen lights off", "set every lamp in the study to 40%" (the resolver turns the phrase into the group)
    python gen_h6.py [N=3500] -> ha_h6_train.jsonl ; adds it to mix_ha.py (safe to run twice)"""
import json, os, random, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_ha as G

LOCKS = ["front door", "back door", "side door", "patio door", "main entrance", "front door lock", "back door lock", "smart lock", "door lock",
         "cellar door", "shed door", "office door", "garage side door", "yale lock", "entrance door", "balcony door"]
GATES = ["garage door", "garage", "gate", "driveway gate", "main gate", "front gate", "back gate", "carport door", "roller door"]
LOCK_T = ["lock {d}", "lock the {d}", "please lock the {d}", "can you lock the {d}", "lock up the {d}", "{d} lock", "make sure the {d} is locked", "secure the {d}",
          "I'm going to bed, lock the {d}", "lock the {d} for me", "could you lock the {d}?", "lock {d} please"]
UNLOCK_T = ["unlock {d}", "unlock the {d}", "please unlock the {d}", "can you unlock the {d}", "let me in, unlock the {d}", "{d} unlock", "unlock the {d} for me",
            "could you unlock the {d}?", "unlock {d} please", "I'm home, unlock the {d}"]
OPEN_T = ["open {d}", "open the {d}", "please open the {d}", "can you open the {d}", "raise the {d}", "{d} open", "open up the {d}", "lift the {d}"]
CLOSE_T = ["close {d}", "close the {d}", "please close the {d}", "can you close the {d}", "lower the {d}", "{d} close", "shut the {d}", "close up the {d}"]
PLURAL = [("lights", "light"), ("lamps", "light"), ("fans", "fan"), ("blinds", "cover"), ("curtains", "cover"), ("shutters", "cover")]
AREA_T = ["all the {p} in the {r}", "all {p} in the {r}", "every {s} in the {r}", "{p} in the {r}", "{r} {p}", "{r} {p}"]


def room_dev(v, rnd, names):
    d = rnd.choice(names)
    if rnd.random() < .35: d = rnd.choice(v.rooms) + " " + d if not d.startswith(("garage", "main", "front", "back", "driveway")) else d
    return d


def one_call(v, rnd):
    x = rnd.random()
    if x < .22:
        d = room_dev(v, rnd, LOCKS); return rnd.choice(LOCK_T).format(d=d), {"name": "HassTurnOn", "arguments": {"name": d}}
    if x < .42:
        d = room_dev(v, rnd, LOCKS); return rnd.choice(UNLOCK_T).format(d=d), {"name": "HassTurnOff", "arguments": {"name": d}}
    if x < .55:
        d = room_dev(v, rnd, GATES); return rnd.choice(OPEN_T).format(d=d), {"name": "HassTurnOn", "arguments": {"name": d}}
    if x < .68:
        d = room_dev(v, rnd, GATES); return rnd.choice(CLOSE_T).format(d=d), {"name": "HassTurnOff", "arguments": {"name": d}}
    p, kind = rnd.choice(PLURAL); s = p[:-1]; r = rnd.choice(v.rooms)
    ph = rnd.choice(AREA_T).format(p=p, s=s, r=r)
    if kind == "light" and rnd.random() < .3:
        n = rnd.choice(range(5, 101, 5))
        return rnd.choice(["set {ph} to {n}%", "dim {ph} to {n}%", "{ph} {n}%"]).format(ph=ph, n=n), {"name": "HassLightSet", "arguments": {"name": ph, "brightness": n}}
    if kind == "cover":
        on = rnd.random() < .5
        return (rnd.choice(["open", "raise"]) if on else rnd.choice(["close", "lower"])) + " " + ph, {"name": "HassTurnOn" if on else "HassTurnOff", "arguments": {"name": ph}}
    on = rnd.random() < .5
    form = rnd.choice(["turn {o} {ph}", "switch {o} {ph}", "{ph} {o}", "turn {ph} {o}"])
    return form.format(o="on" if on else "off", ph=ph), {"name": "HassTurnOn" if on else "HassTurnOff", "arguments": {"name": ph}}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3500
    rnd = random.Random(66); v = G.Vocab("train", rnd)
    rows, seen = [], set()
    for _ in range(n * 4):
        if len(rows) >= n: break
        k = rnd.choices([1, 2], [88, 12])[0]
        parts, calls, devs = [], [], set()
        for _ in range(k):
            q, c = one_call(v, rnd); nm = c["arguments"]["name"].lower()
            if nm in devs or any(nm in p.lower() or p.lower() in nm for p in devs): continue
            devs.add(nm); parts.append(q); calls.append(c)
        if len(calls) != k: continue
        q = parts[0]
        for p in parts[1:]: q += rnd.choice([" and ", ", ", " and also ", ", then "]) + p
        if rnd.random() < .5:
            q2 = q[0].upper() + q[1:]
            if all(c["arguments"]["name"] in q2 for c in calls): q = q2
        if rnd.random() < .4: q = q.rstrip(".?") + rnd.choice([".", "", ""])
        if q.lower() in seen: continue
        seen.add(q.lower()); rows.append(G.make_row(q, calls, "ha_h6"))
    with open(os.path.join(HERE, "ha_h6_train.jsonl"), "w", encoding="utf-8") as fo:
        for r in rows: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} rows -> ha_h6_train.jsonl")
    for r in rows[:10]: print("  ", r["query"], "=>", json.dumps(r["calls"])[:130])
    mp = os.path.join(HERE, "mix_ha.py"); m = open(mp, encoding="utf-8").read()
    if "ha_h6_train" in m: print("mix_ha.py already has ha_h6_train.jsonl")
    else:
        old = '"ha_h5_train.jsonl"]'
        assert m.count(old) == 1, "run gen_h5.py first (it adds ha_h5_train.jsonl to mix_ha.py)"
        open(mp, "w", encoding="utf-8").write(m.replace(old, '"ha_h5_train.jsonl", "ha_h6_train.jsonl"]')); print("mix_ha.py patched")


if __name__ == "__main__":
    main()
