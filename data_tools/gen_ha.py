#!/usr/bin/env python3
# ==========================================================================
# gen_ha.py - automatic natural smart-home requests for the hCAWN-HA English model. No manual work.
# We build the TASK ourselves (tool, device phrase, values) and ask an LLM (OpenRouter) only to PHRASE the request in many ways.
# The gold answer comes from the task, never from the LLM. A version is accepted only if:
#   - every device phrase / list item / color / number from the task appears in it, character for character
#   - the direction is not contradicted (on vs off, open vs close) in the part of the sentence about that device
#   - it asks for nothing extra (checked by the LLM prompt, spot-checked with `test`)
# Calls are ordered by where the device is mentioned in the request (the scorer compares the order).
# Eval uses held-out rooms/items/lists AND a different phrasing style, so it never shares wording with training.
#
#   export OPENROUTER_API_KEY=...            (model: export FC_PARA_MODEL=google/gemini-2.5-flash)
#   python gen_ha.py test                    # 8 tasks, raw answers + accepted ones (cheap, run this first)
#   python gen_ha.py train --n 3000 --k 5    # -> ha_gen_raw_train.jsonl (cache) -> ha_gen_train.jsonl
#   python gen_ha.py eval  --n 600  --k 3    # -> ha_gen_raw_eval.jsonl  -> ha_gen_eval.jsonl
#   python gen_ha.py para --k 3 [--n N]      # 3 new wordings of every converted V2 example (ha_en_train.jsonl) -> ha_para_train.jsonl
#   python gen_ha.py build                   # rebuild the outputs from the cached raw answers (no API calls)
# ==========================================================================
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hcawn_ha"))
import shim                                               # hcawn-fc glue: provides fc_common
from convert_v2 import HA_TOOLS                      # the same 13 tools the converted V2 data uses

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("FC_PARA_MODEL", "google/gemini-2.5-flash")
EVAL_MODEL = os.environ.get("FC_PARA_EVAL_MODEL", MODEL)
RAW = {"train": "ha_gen_raw_train.jsonl", "eval": "ha_gen_raw_eval.jsonl", "para": "ha_para_raw_train.jsonl"}
OUT = {"train": "ha_gen_train.jsonl", "eval": "ha_gen_eval.jsonl", "para": "ha_para_train.jsonl"}
SRC_V2 = "ha_en_train.jsonl"

# ---------------------------------------------------------------- vocabulary (~1 in 5 items is held out for eval)
ROOMS = """living room;kitchen;bedroom;master bedroom;kids' room;bathroom;hallway;garage;attic;basement;office;study;dining room;nursery;
patio;porch;garden;balcony;laundry room;guest room;cellar;conservatory;sunroom;library;gym;workshop;pantry;entryway;stairwell;loft;
terrace;playroom;home theater;mudroom;utility room;shed;greenhouse;den;lounge;studio;back yard;front door;driveway;wine cellar;
upstairs hallway;downstairs bathroom;guest bedroom;second bedroom;boys room;girls room""".replace("\n", "").split(";")
QUAL = ["upstairs", "downstairs", "front", "back", "left", "right", "main", "second"]
KIND = {
    "light": ["light", "lights", "lamp", "ceiling light", "floor lamp", "spotlights", "night light", "reading lamp", "desk lamp", "chandelier"],
    "fan": ["fan", "ceiling fan", "fans", "extractor fan"],
    "switch": ["coffee machine", "kettle", "heater", "air purifier", "dehumidifier", "sprinkler", "string lights", "christmas lights", "towel warmer"],
    "cover": ["blinds", "curtains", "shutters", "shades", "garage door", "skylight blinds"],
    "media": ["speaker", "tv", "stereo", "soundbar", "radio", "music player", "projector"],
    "vacuum": ["vacuum", "robot vacuum", "roomba", "floor cleaner", "mop robot"],
    "climate": ["thermostat", "heating", "air conditioner", "ac"],
}
LISTS = ["shopping list", "grocery list", "to-do list", "packing list", "wish list", "chores list", "birthday gift list", "hardware store list",
         "weekend projects list", "school supplies list", "party planning list", "pharmacy list", "garden tasks list", "holiday checklist", "pet supplies list"]
ITEMS = """milk;eggs;bread;call the plumber;buy light bulbs;batteries;olive oil;pick up the dry cleaning;book a dentist appointment;dog food;paper towels;
bananas;change the air filter;renew car insurance;birthday candles;wrapping paper;laundry detergent;fix the garden gate;toothpaste;cat litter;
charger cable;sunscreen;tomato sauce;vacuum bags;pay the electricity bill;water the plants;coffee beans;lemons;send the parcel;trash bags;
rice;cheese;oranges;sparkling water;check the smoke alarms;order a new mattress;wash the car;pasta;frozen peas;shampoo;buy stamps;
clean the gutters;get a spare key;butter;yogurt;potatoes;onions;garlic;flowers for mom;repair the bike;greeting cards;ice cream""".replace("\n", "").split(";")
COLORS = ["red", "blue", "green", "yellow", "orange", "purple", "pink", "white", "teal", "turquoise", "magenta", "cyan", "lavender", "gold", "coral",
          "indigo", "violet", "lime", "navy", "maroon", "crimson", "salmon", "tomato", "orchid", "limegreen", "skyblue", "hotpink", "darkorange"]


def held_out(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 5 == 0


class Vocab:
    def __init__(self, split, rnd):
        keep = (lambda x: held_out(x)) if split == "eval" else (lambda x: not held_out(x))
        self.rnd = rnd
        self.rooms = [r for r in ROOMS if keep(r)]
        self.lists = [r for r in LISTS if keep(r)]
        self.items = [r for r in ITEMS if keep(r)]
        self.colors = [r for r in COLORS if keep(r)]
        self.kind = {k: [t for t in v if keep(t)] or v for k, v in KIND.items()}

    def device(self, kind):
        r, t = self.rnd, self.rnd.choice(self.kind[kind])
        room = r.choice(self.rooms)
        x = r.random()
        if x < .65:
            return f"{room} {t}"
        if x < .85:
            return f"{t} in the {room}"
        return f"{r.choice(QUAL)} {room} {t}"


# ---------------------------------------------------------------- task sampler
def one(v, rnd, tool=None, allowed=None):
    """One call: dict(tool, kind, dev (phrase or None), vals (extra args), say (how the LLM is told to describe it), musts)."""
    W = [("on", 14), ("off", 14), ("toggle", 4), ("light", 12), ("climate", 7), ("humid", 3), ("list", 8), ("vstart", 3), ("vback", 2), ("media", 6)]
    if allowed:
        W = [w for w in W if w[0] in allowed]
    tool = tool or rnd.choices([w[0] for w in W], [w[1] for w in W])[0]
    if tool in ("on", "off", "toggle"):
        kind = rnd.choices(["light", "fan", "switch", "cover"], [5, 2, 2, 3])[0]
        dev = v.device(kind)
        if kind == "cover":
            say = {"on": f"OPEN (raise) the {dev}", "off": f"CLOSE (lower) the {dev}", "toggle": f"TOGGLE (flip) the {dev}"}[tool]
        else:
            say = {"on": f"turn ON the {dev}", "off": f"turn OFF the {dev}", "toggle": f"TOGGLE (flip) the {dev}"}[tool]
        return dict(tool={"on": "HassTurnOn", "off": "HassTurnOff", "toggle": "HassToggle"}[tool], kind=kind, dev=dev, vals={}, say=say, musts=[dev])
    if tool == "light":
        dev = v.device("light")
        if rnd.random() < .5:
            n = rnd.choice([rnd.randint(1, 100), rnd.choice(range(5, 101, 5))])
            return dict(tool="HassLightSet", kind="light", dev=dev, vals={"brightness": n}, say=f"set the brightness of the {dev} to {n} percent", musts=[dev, str(n)])
        c = rnd.choice(v.colors)
        return dict(tool="HassLightSet", kind="light", dev=dev, vals={"color": c}, say=f"change the color of the {dev} to {c}", musts=[dev, c])
    if tool == "climate":
        n = rnd.choice(range(16, 31)) if rnd.random() < .6 else rnd.choice(range(60, 81))
        dev = v.device("climate") if rnd.random() < .45 else None
        say = f"set the {dev} to {n} degrees" if dev else f"set the temperature to {n} degrees (no device is named)"
        return dict(tool="HassClimateSetTemperature", kind="climate", dev=dev, vals={"temperature": n}, say=say, musts=([dev] if dev else []) + [str(n)])
    if tool == "humid":
        n = rnd.randint(25, 75)
        return dict(tool="HassHumidifierSetpoint", kind="humidifier", dev=None, vals={"humidity": n}, say=f"set the humidity to {n} percent (no device is named)", musts=[str(n)])
    if tool == "list":
        lst, it = rnd.choice(v.lists), rnd.choice(v.items)
        return dict(tool="HassListAddItem", kind="list", dev=lst, vals={"item": it}, say=f"add the item '{it}' to the list '{lst}'", musts=[lst, it])
    if tool in ("vstart", "vback"):
        dev = v.device("vacuum")
        return dict(tool="HassVacuumStart" if tool == "vstart" else "HassVacuumReturnToBase", kind="vacuum", dev=dev, vals={},
                    say=(f"start the {dev}" if tool == "vstart" else f"send the {dev} back to its dock/base"), musts=[dev])
    kind = "media"
    dev = v.device("media")
    t, say = rnd.choice([("HassMediaPause", "pause playback on the {d}"), ("HassMediaUnpause", "resume (play) the {d}"),
                         ("HassMediaNext", "skip to the next track on the {d}"), ("HassMediaPrevious", "go to the previous track on the {d}")])
    return dict(tool=t, kind=kind, dev=dev, vals={}, say=say.format(d=dev), musts=[dev])


def sample_task(v, rnd):
    x = rnd.random()
    if x < .07:
        return {"kind": "none", "calls": []}
    if x < .27:
        n = rnd.choice([2, 2, 3])
        calls, devs = [], set()
        for _ in range(20):
            c = one(v, rnd, allowed={"on", "off", "toggle", "light", "list", "vstart", "vback", "media"})     # multi: calls that name a device
            key = c["dev"].lower()
            if key not in devs:
                devs.add(key)
                calls.append(c)
            if len(calls) == n:
                break
        if len(calls) >= 2:
            return {"kind": "multi", "calls": calls}
    return {"kind": "single", "calls": [one(v, rnd)]}


# ---------------------------------------------------------------- LLM
STYLES = {
    "train": """Write {k} different versions, each as a DIFFERENT real person would say it to a smart home assistant:
 1. very short and casual (like a text message, no politeness words)
 2. polite and indirect ("Could you ...", "I'd like ...", "Would you mind ...")
 3. everyday verbs ("kill", "shut off", "crank up", "dim", "pop on", "fire up", "put on", "get rid of")
 4. the request inside a short sentence of context ("I'm heading to bed, ...", "My hands are full, ...")""",
    "eval": """Write {k} versions the way different real users would actually type or say it to a home assistant: vary verbs, word order, politeness and the amount of context. Some blunt, some chatty, some with a reason.""",
}
PROMPT = """You write requests for a smart home voice assistant.

THE USER WANTS THIS DONE (all of it, nothing else):
{actions}

TEXT THAT MUST APPEAR IN EVERY VERSION, CHARACTER FOR CHARACTER (do not change, pluralize, reorder, abbreviate or translate it):
{musts}

{styles}

Rules:
- Every version asks for ALL the listed actions and nothing else. Do not add devices, rooms, times, other values or facts.
- Keep every required text exactly as written, together with its original words, and do NOT put quotation marks around it.
- Numbers stay as digits. Percent may be written "percent" or "%".
- Never mention functions, tools, JSON, APIs, Home Assistant or the word "intent".
- Each version is one request in English, different from each other.
Return ONLY a JSON array of {k} strings."""
PARA_PROMPT = """You rewrite requests for a smart home voice assistant.

ORIGINAL REQUEST:
{query}

TEXT THAT MUST APPEAR IN EVERY VERSION, CHARACTER FOR CHARACTER (do not change, pluralize, reorder, abbreviate or translate it):
{musts}

{styles}

Rules:
- Keep exactly the same meaning and the same actions. If the original asks for several actions, every version asks for all of them.
- Keep every required text exactly as written, together with its original words, and do NOT put quotation marks around it.
- Do not add devices, rooms, times, other values or facts. Numbers stay as digits.
- Never mention functions, tools, JSON, APIs, Home Assistant or the word "intent".
- Each version is one request in English, different from the original and from each other.
Return ONLY a JSON array of {k} strings."""
NONE_PROMPT = """Write {k} requests that a person might say to a smart home voice assistant but that are NOT device commands the assistant can do. Mix:
 - questions about the weather, time, news, jokes, math, recipes
 - asking whether a device is on, off, open or locked ("is the front door locked")
 - requests the assistant cannot do (set an alarm, call someone, order food, play a specific song, lock or unlock a door)
 - small talk
Each one short and natural. Do not mention functions, tools or JSON.
Return ONLY a JSON array of {k} strings."""


def call_llm(prompt, model, temperature=1.0, retries=5):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("No OPENROUTER_API_KEY (export OPENROUTER_API_KEY=...)")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "max_tokens": 1200}).encode()
    for a in range(retries):
        try:
            req = urllib.request.Request(URL, body, {"Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Title": "hcawn-ha-gen"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                sys.exit(f"API error {e.code}: {e.read().decode(errors='replace')[:200]}")
            time.sleep(2 ** a + random.random())
        except Exception:
            time.sleep(2 ** a + random.random())
    return None


def parse_list(txt):
    m = re.search(r"\[.*\]", txt or "", re.S)
    try:
        arr = json.loads(m.group(0)) if m else []
    except Exception:
        return []
    return [a.strip() for a in arr if isinstance(a, str) and a.strip()]


COVER_WORDS = {"blinds", "blind", "curtains", "curtain", "shutters", "shutter", "shades", "shade", "garage", "door", "skylight"}


def task_from_row(r):
    """Rebuild the checkable task from a converted V2 row (calls with name/values)."""
    calls = []
    for c in r["calls"]:
        a = dict(c["arguments"])
        dev = a.pop("name", None)
        words = set(re.findall(r"[a-z]+", (dev or "").lower()))
        calls.append(dict(tool=c["name"], kind="cover" if words & COVER_WORDS else "x", dev=dev, vals=a, say="",
                          musts=([dev] if dev else []) + [str(x) for x in a.values()]))
    return {"kind": "multi" if len(calls) > 1 else "single", "calls": calls, "orig": r["query"]}


def prompt_for(task, split, k):
    if split == "para":
        musts = "\n".join(f"- {m}" for c in task["calls"] for m in c["musts"])
        return PARA_PROMPT.format(query=task["orig"], musts=musts, styles=STYLES["train"].format(k=k), k=k)
    if task["kind"] == "none":
        return NONE_PROMPT.format(k=k)
    actions = "\n".join(f"- {c['say']}" for c in task["calls"])
    musts = "\n".join(f"- {m}" for c in task["calls"] for m in c["musts"])
    return PROMPT.format(actions=actions, musts=musts, styles=STYLES[split].format(k=k), k=k)


# ---------------------------------------------------------------- acceptance
BAD = re.compile(r"\b(?:function|json|api|tool|intent|home assistant|hass)\b", re.I)
ON = re.compile(r"\b(?:on|enable|activate|start|light up|power up|fire up|switch on|turn up)\b", re.I)
OFF = re.compile(r"\b(?:off|disable|kill|shut|deactivate|stop|out|extinguish|power down|turn down)\b", re.I)
OPEN = re.compile(r"\b(?:open|raise|lift|up|uncover)\b", re.I)
CLOSE = re.compile(r"\b(?:close|shut|lower|down|cover)\b", re.I)


def find(q, s):
    m = re.search(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])", q, re.I)
    return m


def clause(q, dev):
    parts = [p for p in re.split(r"\b(?:and|then|also|plus)\b|[,;]", q) if p.strip()]
    for p in parts:
        if dev.lower() in p.lower():
            return p
    return q


def accept_calls(task, q):
    """Return the gold calls (ordered by position in q, values as written in q) or None."""
    got = []
    for c in task["calls"]:
        pos, args = None, {}
        if c["dev"]:
            m = find(q, c["dev"])
            if not m:
                return None
            pos, args["name"] = m.start(), m.group(0)
        for m_ in c["musts"]:
            if c["dev"] and m_ == c["dev"]:
                continue
            mm = find(q, m_)
            if not mm:
                return None
            if pos is None:
                pos = mm.start()
        for k, v in c["vals"].items():
            if k == "item":
                args[k] = find(q, v).group(0)
            else:
                args[k] = v
        part = clause(q, c["dev"]) if c["dev"] else q
        t = c["tool"]
        cover = c["kind"] == "cover"
        if t == "HassTurnOn" and (CLOSE.search(part) if cover else OFF.search(part)):
            return None
        if t == "HassTurnOff" and (OPEN.search(part) if cover else ON.search(part)):
            return None
        if t == "HassLightSet" and (ON.search(part) or OFF.search(part) or "get rid" in part.lower()
                                    or ("color" in c["vals"] and re.search(r"\b(dim|dimmer|dimming|brighten|bright|brightness)\b", part, re.I))):
            return None
        if t == "HassToggle" and (ON.search(part) or OFF.search(part) or not re.search(r"\b(toggle|flip|switch)\b", part, re.I)):
            return None
        got.append((pos, {"name": t, "arguments": args}))
    if len(task["calls"]) > 1 and len({p for p, _ in got}) < len(got):
        return None
    return [c for _, c in sorted(got, key=lambda x: x[0])]


def accept(task, q, seen):
    q = " ".join(q.split())
    q = q.replace('"', "")
    if not 8 <= len(q) <= 220 or q.lower() in seen or BAD.search(q):
        return None
    if task["kind"] == "none":
        return q, []
    calls = accept_calls(task, q)
    return (q, calls) if calls else None


# ---------------------------------------------------------------- dirty typing (values and device phrases stay intact)
FILL_A, FILL_B = ["hey", "ok", "yo", "hi"], ["pls", "thx", "please", "now"]


def dirty(task, q, rnd):
    protected = set()
    for c in task["calls"]:
        for m in c["musts"]:
            protected |= set(re.findall(r"[a-z0-9']+", m.lower()))
    x = rnd.random()
    if x < .5:
        q = q.lower()
    q = q.rstrip(".!?") if rnd.random() < .6 else q
    words = q.split(" ")
    cand = [i for i, w in enumerate(words) if len(w) >= 5 and re.sub(r"[^a-z0-9']", "", w.lower()) not in protected and w.isalpha()]
    if cand and rnd.random() < .5:
        i = rnd.choice(cand)
        w = words[i]
        j = rnd.randrange(1, len(w) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
        q = " ".join(words)
    if rnd.random() < .2:
        q = rnd.choice(FILL_A) + " " + q
    elif rnd.random() < .2:
        q = q + " " + rnd.choice(FILL_B)
    return q


# ---------------------------------------------------------------- driver
def make_row(q, calls, src="ha_gen"):
    return {"src": src, "tools": HA_TOOLS, "context": "", "query": q, "calls": calls}


def request(split, n, k, workers=8):
    rnd = random.Random({"train": 1, "eval": 2, "para": 3}[split] * 1000 + n)
    v = Vocab("eval" if split == "eval" else "train", rnd)
    model = EVAL_MODEL if split == "eval" else MODEL
    done = {}
    if os.path.exists(RAW[split]):
        for l in open(RAW[split], encoding="utf-8"):
            d = json.loads(l)
            done[d["i"]] = d
    if split == "para":
        rows = [json.loads(l) for l in open(SRC_V2, encoding="utf-8") if l.strip()]
        tasks = [task_from_row(r) for r in (rows[:n] if n else rows)]
        n = len(tasks)
    else:
        tasks = [sample_task(v, rnd) for _ in range(n)]
    todo = [i for i in range(n) if i not in done]
    lock = threading.Lock()
    fo = open(RAW[split], "a", encoding="utf-8")

    def work(i):
        txt = call_llm(prompt_for(tasks[i], split, k), model)
        if txt is None:
            return
        with lock:
            fo.write(json.dumps({"i": i, "task": tasks[i], "paras": parse_list(txt)}, ensure_ascii=False) + "\n")
            fo.flush()

    print(f"{split}: {len(todo)} requests to {model} ({len(done)} cached)")
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, todo))
    fo.close()


def build(split):
    if not os.path.exists(RAW[split]):
        return
    rnd = random.Random(99)
    rows, st, seen_all = [], {"tasks": 0, "proposed": 0, "accepted": 0}, set()
    for l in open(RAW[split], encoding="utf-8"):
        d = json.loads(l)
        st["tasks"] += 1
        seen = set()
        for p in d["paras"]:
            st["proposed"] += 1
            a = accept(d["task"], p, seen)
            if not a:
                continue
            q, calls = a
            if split in ("train", "para") and rnd.random() < .3:
                q2 = dirty(d["task"], q, rnd)
                b = accept(d["task"], q2, seen) if d["task"]["kind"] != "none" else (q2, [])
                if b:
                    q, calls = b
            if q.lower() in seen_all:
                continue
            seen.add(q.lower())
            seen_all.add(q.lower())
            rows.append(make_row(q, calls))
            st["accepted"] += 1
    with open(OUT[split], "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    per = {}
    for r in rows:
        key = "+".join(sorted({c["name"] for c in r["calls"]})) or "none"
        per[key] = per.get(key, 0) + 1
    print(f"{split}: {st} -> {OUT[split]}")
    for k_, v_ in sorted(per.items(), key=lambda x: -x[1])[:25]:
        print(f"   {v_:6d}  {k_}")


def test():
    rnd = random.Random(5)
    v = Vocab("train", rnd)
    for _ in range(8):
        t = sample_task(v, rnd)
        txt = call_llm(prompt_for(t, "train", 4), MODEL)
        print("\nTASK:", [c["say"] for c in t["calls"]] or "(no-call)")
        seen = set()
        for p in parse_list(txt):
            a = accept(t, p, seen)
            print("  ", "OK  " if a else "DROP", p, ("=> " + json.dumps(a[1], ensure_ascii=False)) if a else "")


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    arg = lambda f, d: int(a[a.index(f) + 1]) if f in a else d
    if a[0] == "test":
        test()
    elif a[0] in ("train", "eval", "para"):
        request(a[0], arg("--n", 0 if a[0] == "para" else 600), arg("--k", 3 if a[0] == "para" else 4))
        build(a[0])
    elif a[0] == "build":
        build("train")
        build("eval")
        build("para")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
