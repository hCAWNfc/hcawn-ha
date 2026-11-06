#!/usr/bin/env python3
"""Convert Home-Assistant-Requests-V2 (English) to the flat training format used by the fc models:
    {"query": str, "calls": [{"name": str, "arguments": {...}}]}
The device list is NOT kept in the prompt: the model must name the device the way the request says it (a resolver maps it to
an entity later). Labels are repaired by rules, examples that cannot be repaired are dropped and counted.

    python convert_v2.py                      # train + test -> ha_en_train.jsonl, ha_en_eval.jsonl, convert_report.txt
"""
import collections
import itertools
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hcawn_ha._core import fc_common                        # prompt format of the engine package
sys.modules.setdefault("fc_common", fc_common)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fc_common import normalize_tool, render_tool        # the trainer prompt format lives in sft/fc/fc_common.py

SRC = {"train": "data_v2/home_assistant_train_english.jsonl", "eval": "data_v2/home_assistant_test_english.jsonl"}
OUT = {"train": "ha_en_train.jsonl", "eval": "ha_en_eval.jsonl"}
MAXQ = 300
DEV = re.compile(r"^([a-z_]+\.[a-z0-9_]+) '([^']*)' = ?(.*)$")
ENT = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
CSS = set("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue
chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgrey darkgreen darkkhaki
darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite
gold goldenrod gray grey green greenyellow honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgrey lightgreen lightpink lightsalmon lightseagreen lightskyblue
lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid
mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
peachpuff peru pink plum powderblue purple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow
yellowgreen""".split())

CAP_LIGHT = {"train": 2500, "eval": 100}      # HassLightSet-only examples are ~65% of V2; keep a share, own data fills the rest


def _tool(name, desc, props, req):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": {k: {"type": t} for k, t in props.items()}, "required": req}}


_N = {"name": "string"}
HA_TOOLS = [normalize_tool(t) for t in [
    _tool("HassTurnOn", "Turn on, open or start a device", _N, ["name"]),
    _tool("HassTurnOff", "Turn off, close or stop a device", _N, ["name"]),
    _tool("HassToggle", "Toggle a device on or off", _N, ["name"]),
    _tool("HassLightSet", "Set light brightness (0-100) or color", {"name": "string", "brightness": "integer", "color": "string"}, ["name"]),
    _tool("HassClimateSetTemperature", "Set the target temperature", {"name": "string", "temperature": "number"}, ["temperature"]),
    _tool("HassHumidifierSetpoint", "Set the target humidity in percent", {"name": "string", "humidity": "integer"}, ["humidity"]),
    _tool("HassListAddItem", "Add an item to a list", {"name": "string", "item": "string"}, ["name", "item"]),
    _tool("HassVacuumStart", "Start the vacuum", _N, ["name"]),
    _tool("HassVacuumReturnToBase", "Send the vacuum back to its base", _N, ["name"]),
    _tool("HassMediaPause", "Pause playback", _N, ["name"]),
    _tool("HassMediaUnpause", "Resume playback", _N, ["name"]),
    _tool("HassMediaNext", "Next track", _N, ["name"]),
    _tool("HassMediaPrevious", "Previous track", _N, ["name"]),
]]
TOOLS_BYTES = len("\n".join(render_tool(t) for t in HA_TOOLS).encode())

drop = collections.Counter()
kept_tools = collections.Counter()
samples_drop = collections.defaultdict(list)
samples_keep = collections.defaultdict(list)
queries = {}


def txt(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""


def toks(s):
    return re.findall(r"[a-z0-9]+", s.lower())


def _m(t, q):
    """Does name word t match request word q (equal, or a shared prefix of >= 3 letters to tolerate plurals)."""
    if t == q:
        return True
    n = min(len(t), len(q), 5)
    return n >= 3 and t[:n] == q[:n]


DEVW = set("fan fans light lights lamp lamps blind blinds shade shades curtain curtains shutter shutters vacuum tv speaker speakers "
           "lock door garage switch heater thermostat humidifier".split())


def derivable(name, query):
    """Every word of the device name appears in the request."""
    qt = toks(query)
    return all(any(_m(t, q) for q in qt) for t in toks(name))


def phrase(query, name):
    """The device as the user said it: the shortest stretch of the request holding all words of the name, widened by an
    adjacent device-type word (fan, light, blinds, ...). Verbatim text from the request, or None."""
    low = query.lower()
    if len(low) != len(query):
        return None
    qs = [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-z0-9]+", low)]
    nt = toks(name)
    if not nt:
        return None
    cands = [[i for i, (q, _, _) in enumerate(qs) if _m(t, q)] for t in nt]
    if any(not c for c in cands):
        return None
    best = None
    for combo in itertools.product(*[c[:4] for c in cands]):
        if len(set(combo)) < len(combo):
            continue
        w = max(combo) - min(combo)
        if best is None or w < best[0] or (w == best[0] and min(combo) < min(best[1])):
            best = (w, combo)
    if best is None:
        return None
    lo, hi = min(best[1]), max(best[1])
    if lo > 0 and qs[lo - 1][0] in DEVW:
        lo -= 1
    elif hi + 1 < len(qs) and qs[hi + 1][0] in DEVW:
        hi += 1
    return query[qs[lo][1]:qs[hi][2]]


def span(query, value):
    """The value as written in the request (verbatim), or None."""
    i = query.lower().find(str(value).lower())
    return query[i:i + len(str(value))] if i >= 0 else None


def light_values(query, name):
    nt = set(toks(name))
    out = {}
    m = re.search(r"(\d{1,3})\s*(?:%|percent)", query.lower())
    if not m and re.search(r"\b(brighten|dim|brightness)\b", query.lower()):
        m = re.search(r"\bto\s+(\d{1,3})\b", query.lower())
    if m and 0 <= int(m.group(1)) <= 100:
        out["brightness"] = int(m.group(1))
    qt = toks(query)
    for i, t in enumerate(qt):
        two = t + qt[i + 1] if i + 1 < len(qt) else ""
        if two in CSS and t not in nt:
            out["color"] = two
            break
        if t in CSS and t not in nt:
            out["color"] = t
            break
    return out


def clause_of(query, nm):
    """The part of the request (split on and/then/commas) that talks about this device."""
    parts = [p for p in re.split(r"\b(?:and|then|also|plus)\b|[,;]", query) if p.strip()]
    if len(parts) < 2 or not nm:
        return query
    nt = toks(nm)
    best = max(parts, key=lambda p: sum(any(_m(t, q) for q in toks(p)) for t in nt))
    return best


def fix_call(tool, a, query, reg):
    """Return (call, None) or (None, reason)."""
    nm = a.get("name")
    ent = None
    if nm is not None:
        if ENT.match(str(nm)):
            if nm in reg:
                ent, nm = nm, reg[nm][0]
            else:
                ent = nm
                dom_, rest = nm.split(".", 1)
                nm = (rest.replace("_", " ") + " " + dom_) if dom_ not in rest else rest.replace("_", " ")
        else:
            ent = next((e for e, (f, _) in reg.items() if f.lower() == str(nm).lower()), None)
    dom = ent.split(".")[0] if ent else None
    q = clause_of(query, nm).lower()
    qwhole = query.lower()
    if dom == "lock":
        return None, "lock (convention unclear, own templates later)"
    if dom == "timer":
        return None, "timer domain (labels are wrong)"
    args = {}
    has_name = nm is not None and derivable(nm, query)
    ph = phrase(query, nm) if has_name else None
    if tool in ("HassClimateSetTemperature", "HassHumidifierSetpoint"):
        if set(a) - {"name", "temperature", "humidity"}:
            return None, "non-standard climate args (fan_mode/hvac_mode)"
        key = "temperature" if tool == "HassClimateSetTemperature" else "humidity"
        if key not in a or str(a[key]) not in re.findall(r"\d+(?:\.\d+)?", query):
            return None, "value not in request"
        if ph:
            args["name"] = ph
        args[key] = a[key]
        return {"name": tool, "arguments": args}, None
    if not ph:
        return None, "device name not recoverable from request"
    args["name"] = ph
    if tool == "HassListAddItem":
        it = span(query, a.get("item", ""))
        if not it:
            return None, "item not verbatim in request"
        args["item"] = it
        return {"name": tool, "arguments": args}, None
    if tool == "HassSetVolume":
        return None, "set volume (value missing in labels)"
    if tool in ("HassTurnOn", "HassTurnOff", "HassToggle") and dom in ("light", None):
        vals = light_values(query, nm) if tool == "HassTurnOn" else {}
        if vals:
            args.update(vals)
            return {"name": "HassLightSet", "arguments": args}, None
        rest = " ".join(t for t in toks(q) if t not in set(toks(nm)))
        if dom == "light" and tool == "HassTurnOn" and re.search(r"colou?r|percent|%|\bbrighten|\bdim\b", rest):
            return None, "light value words but no parsable value"
    if tool == "HassToggle" and not re.search(r"\btoggle|flip\b", q):
        return None, "toggle without toggle word"
    if tool == "HassTurnOn" and re.search(r"\b(off|close|closed|shut|lock)\b", q):
        return None, "verb contradicts TurnOn"
    if tool == "HassTurnOff" and re.search(r"\b(on|open|opened|raise|unlock)\b", q):
        return None, "verb contradicts TurnOff"
    if dom == "fan" and re.search(r"\b(slow|speed|faster|slower|lower|accelerat\w*|decelerat\w*|ramp|boost|reduce|increase|decrease)\b|turn (up|down)", qwhole):
        return None, "fan speed (not expressible)"
    return {"name": tool, "arguments": args}, None


def run(split):
    rows, seen = [], set()
    with open(SRC[split], encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            ms = d["messages"]
            sysm = txt(ms[0])
            reg = {}
            if "Devices:\n" in sysm:
                for ln in sysm.split("Devices:\n", 1)[1].split("\n"):
                    mm = DEV.match(ln.strip())
                    if mm:
                        reg[mm.group(1)] = (mm.group(2), mm.group(3))
            query = next((txt(m) for m in ms if m["role"] == "user"), "").strip()
            raw = []
            for m in ms:
                for tc in (m.get("tool_calls") or []):
                    f = tc.get("function") or tc
                    try:
                        raw.append((f["name"], json.loads(f.get("arguments") or "{}")))
                    except Exception:
                        pass
            if not raw:
                drop["no tool call (state question / not found)"] += 1
                continue
            if not query or len(query) > MAXQ:
                drop["empty or too long"] += 1
                continue
            if len(raw) > 1:
                names = [str(a.get("name", "")).lower() for _, a in raw]
                if len(set(names)) < len(names) or any(ENT.match(n) for n in names):
                    drop["multi-call with duplicate / entity_id targets"] += 1
                    continue
            calls, why = [], None
            for tool, a in raw:
                c, why = fix_call(tool, a, query, reg)
                if c is None:
                    break
                calls.append(c)
            if why:
                drop[why] += 1
                if len(samples_drop[(split, why)]) < 10:
                    samples_drop[(split, why)].append((query, raw))
                continue
            key = (query.lower(), json.dumps(calls, sort_keys=True))
            if key in seen:
                drop["duplicate"] += 1
                continue
            if split == "eval" and query.lower() in queries.get("train", set()):
                drop["eval query also in train"] += 1
                continue
            seen.add(key)
            for c in calls:
                kept_tools[(split, c["name"])] += 1
                if len(samples_keep[(split, c["name"])]) < 5:
                    samples_keep[(split, c["name"])].append((query, calls))
            queries.setdefault(split, set()).add(query.lower())
            rows.append({"src": "ha_v2", "tools": HA_TOOLS, "context": "", "query": query, "calls": calls})
    rnd = random.Random(7)
    light = [r for r in rows if all(c["name"] == "HassLightSet" for c in r["calls"])]
    if len(light) > CAP_LIGHT[split]:
        rnd.shuffle(light)
        cut = set(id(r) for r in light[CAP_LIGHT[split]:])
        drop["HassLightSet-only over cap (kept %d)" % CAP_LIGHT[split]] += len(cut)
        rows = [r for r in rows if id(r) not in cut]
        for r in light[CAP_LIGHT[split]:]:
            for c in r["calls"]:
                kept_tools[(split, c["name"])] -= 1
    with open(OUT[split], "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def main():
    lines = ["tools block in the prompt: %d bytes (training MAX_LEN is 2048 for prompt + answer)" % TOOLS_BYTES]
    for split in ("train", "eval"):
        drop.clear()
        n = run(split)
        lines.append(f"== {split}: kept {n} -> {OUT[split]}")
        for k, v in drop.most_common():
            lines.append(f"   dropped {v:6d}  {k}")
        lines.append("   kept calls per tool:")
        for (s, t), v in sorted(kept_tools.items(), key=lambda x: -x[1]):
            if s == split:
                lines.append(f"      {t:28s} {v}")
    out = "\n".join(lines)
    print(out)
    open("convert_report.txt", "w").write(out + "\n")
    sm = ["# eval queries that also occur in train: %d of %d" % (len(queries.get("train", set()) & queries.get("eval", set())), len(queries.get("eval", set()))), ""]
    sm.append("## KEPT samples")
    for (sp, t), v in sorted(samples_keep.items()):
        if sp == "train":
            sm.append("-- " + t)
            for q, c in v:
                sm.append("   " + q + "  =>  " + json.dumps(c, ensure_ascii=False))
    sm.append("\n## DROPPED samples (train): request  <=  original labels")
    for (sp, why), v in sorted(samples_drop.items()):
        if sp == "train":
            sm.append("-- " + why)
            for q, raw in v:
                sm.append("   " + q + "  <=  " + json.dumps(raw, ensure_ascii=False)[:160])
    open("convert_samples.txt", "w").write("\n".join(sm) + "\n")
    print("\nsamples -> convert_samples.txt")


if __name__ == "__main__":
    main()
