"""python tests/test_eval_phrases.py path/to/ha_eval.jsonl
Every device phrase in the eval set becomes one entity of a big synthetic registry; then every gold call is resolved again.
Harsher than a real house (thousands of similar names), so some 'held' answers are expected - 'wrong' must stay 0."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hcawn_ha"))
import json, sys, time, collections
from ha_resolver import resolve, tokens

DOM = {"HassLightSet": "light", "HassClimateSetTemperature": "climate", "HassHumidifierSetpoint": "humidifier", "HassListAddItem": "todo",
       "HassVacuumStart": "vacuum", "HassVacuumReturnToBase": "vacuum", "HassMediaPause": "media_player", "HassMediaUnpause": "media_player",
       "HassMediaNext": "media_player", "HassMediaPrevious": "media_player"}
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
calls = [(c["name"], c["arguments"].get("name")) for r in rows for c in r["calls"] if c["arguments"].get("name")]
NAME_DOM = {}
for t, n in calls:                      # one type per name: a specific tool (media, vacuum, ...) wins over on/off/toggle guesses
    if t in DOM:
        NAME_DOM[n.lower()] = DOM[t]
for t, n in calls:
    if n.lower() not in NAME_DOM:
        low = n.lower()                 # on/off/toggle do not say what the device is -> guess from the words
        NAME_DOM[low] = "cover" if any(w in low for w in ("blind", "curtain", "shutter", "shade")) else "fan" if "fan" in low else "light"


def dom_of(t, n):
    return NAME_DOM[n.lower()]


def canon(t, n):        # same device if it has the same words, whatever the order ("patio stereo" = "stereo in the patio")
    return (dom_of(t, n), frozenset(tokens(n)))


reg, key = [], {}
for t, n in calls:
    k = canon(t, n)
    if k not in key:
        key[k] = f"{k[0]}.e{len(reg)}"
        reg.append({"entity_id": key[k], "name": n, "area": None, "aliases": [], "device_class": None})
print(f"registry: {len(reg)} entities from {len(calls)} gold calls")
st, t0, bad, held = collections.Counter(), time.time(), [], []
for i, (t, n) in enumerate(calls):
    want = key[canon(t, n)]
    r = resolve(n, reg, t)
    if r["status"] == "ok":
        good = want in r["entity_ids"]
        st["ok_correct" if good else "WRONG"] += 1
        if not good and len(bad) < 8: bad.append((t, n, r["entity_ids"], want))
    elif r["status"] == "held" and len(held) < 10:
        held.append((t, n, [c["name"] for c in r["candidates"]]))
    else:
        st[r["status"]] += 1
    if i % 100 == 0:
        print(f"\r{i}/{len(calls)} | {dict(st)} | {time.time() - t0:.0f}s   ", end="", flush=True)
print(f"\r{len(calls)}/{len(calls)} | {dict(st)} | {time.time() - t0:.0f}s   ")
tot = sum(st.values())
print({k: f"{100 * v / tot:.1f}%" for k, v in st.items()}, " <- WRONG must be 0")
for b in bad: print("  WRONG:", b)
for h in held: print("  HELD:", h)
