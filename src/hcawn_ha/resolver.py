"""ha_resolver.py - maps the device phrase the model copies from the request to a Home Assistant entity. Standard library only.

    from ha_resolver import resolve, resolve_calls, to_service
    r = resolve("kitchen light", registry, tool="HassTurnOn")
    # r["status"]: "ok" (r["entity_ids"] has the match) | "held" (ambiguous: ask r["message"]) | "none" (nothing close)

registry = list of dicts, exported from Home Assistant (entity registry + area registry):
    {"entity_id": "light.kitchen_ceiling", "name": "Kitchen ceiling", "area": "Kitchen", "aliases": ["main light"], "device_class": None}

Safety: locks / garage doors / alarms always come back with confirm=True, and lock actions are NOT turned into service calls in v1.
"""
import difflib
import re

OK_MIN, MARGIN, HELD_MIN = 0.70, 0.12, 0.45

STOP = {"the", "a", "an", "my", "our", "of", "in", "on", "at", "to", "please", "all", "every", "for", "and", "this", "that", "your", "one", "ones", "thing", "device"}
TYPE = {  # token (singular) -> (domain, synonym class)
    **{w: ("light", "light") for w in ("light", "lamp", "chandelier", "sconce", "bulb", "lantern", "lighting")},
    "fan": ("fan", "fan"),
    **{w: ("vacuum", "vacuum") for w in ("vacuum", "roomba", "robot", "hoover")},
    **{w: ("media_player", "media") for w in ("tv", "television", "stereo", "speaker", "radio", "soundbar", "player", "chromecast")},
    **{w: ("cover", "cover") for w in ("blind", "curtain", "shutter", "shade", "garage", "gate", "skylight", "awning")},
    **{w: ("climate", "climate") for w in ("heater", "thermostat", "ac", "radiator", "heating")},
    "humidifier": ("humidifier", "humidifier"),
    "lock": ("lock", "lock"),
    **{w: ("switch", "switch") for w in ("plug", "outlet", "socket")},
    "list": ("todo", "list"),
}
TOOL_DOMAINS = {
    "HassTurnOn": {"light", "switch", "fan", "cover", "media_player", "vacuum", "humidifier", "climate", "lock", "input_boolean"},
    "HassTurnOff": {"light", "switch", "fan", "cover", "media_player", "vacuum", "humidifier", "climate", "lock", "input_boolean"},
    "HassToggle": {"light", "switch", "fan", "cover", "media_player", "humidifier", "input_boolean"},
    "HassLightSet": {"light"},
    "HassClimateSetTemperature": {"climate"},
    "HassHumidifierSetpoint": {"humidifier"},
    "HassListAddItem": {"todo"},
    "HassVacuumStart": {"vacuum"},
    "HassVacuumReturnToBase": {"vacuum"},
    "HassMediaPause": {"media_player"}, "HassMediaUnpause": {"media_player"},
    "HassMediaNext": {"media_player"}, "HassMediaPrevious": {"media_player"},
}
NEEDS_CONFIRM_DOMAINS = {"lock", "alarm_control_panel"}
NEEDS_CONFIRM_WORDS = {"garage", "gate"}
WEAK_TYPE = {"garage", "gate", "player", "robot"}   # words that are often part of a room/brand name


def _sing(t):
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def tokens(text):
    out = []
    for t in re.findall(r"[a-z0-9]+", str(text).lower().replace("'", "")):
        if t not in STOP:
            out.append(_sing(t))
    return out


def _sim(a, b):
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0
    if a in TYPE and b in TYPE and TYPE[a][1] == TYPE[b][1]:
        return 0.6
    if min(len(a), len(b)) >= 4:
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r >= 0.8:
            return 0.95 * r
    return 0.0


def _w(t):
    return 0.5 if t in TYPE else 1.0


def _score(P, N, A):
    """P phrase tokens, N entity-name tokens, A area tokens -> 0..1"""
    if not P or not N:
        return 0.0
    rec = rw = 0.0
    unknown_word = False
    used_n = {}
    for p in P:
        best, bn = 0.0, None
        for n in N:
            s = _sim(p, n)
            if s > best:
                best, bn = s, n
        for a in A:
            s = 0.9 * _sim(p, a)
            if s > best:
                best, bn = s, None
        rec += _w(p) * best
        rw += _w(p)
        if best == 0.0 and (p not in TYPE or p in WEAK_TYPE):
            unknown_word = True
        if bn is not None:
            used_n[bn] = max(used_n.get(bn, 0.0), best)
    pw = sum(_w(n) for n in N)
    prec = sum(_w(n) * used_n.get(n, 0.0) for n in N) / pw
    sc = 0.85 * rec / rw + 0.15 * prec
    if unknown_word:
        sc = min(sc, 0.4)          # the user named something this device has no trace of ("garage lamp" vs a bedroom lamp)
    return sc


def _entity_score(P, e):
    A = tokens(e.get("area") or "")
    names = [e.get("name") or e["entity_id"].split(".", 1)[1].replace("_", " ")] + list(e.get("aliases") or [])
    best, exact = 0.0, False
    for nm in names:
        N = tokens(nm)
        if set(P) == set(N) or set(P) == set(N) | set(A):
            return 1.0, True
        best = max(best, _score(P, N, A), _typo_score(P, N, A))
    return best, exact


def _typo_score(P, N, A):
    """Whole-phrase spelling check (handles 'kdis room radio', 'livingr oom speaker'); digits must match exactly."""
    if [t for t in P if t.isdigit()] != sorted(t for t in N if t.isdigit()) and set(t for t in P if t.isdigit()) != set(t for t in N if t.isdigit()):
        return 0.0
    p = "".join(sorted(P))
    r = max(difflib.SequenceMatcher(None, p, "".join(sorted(N + A))).ratio(),
            difflib.SequenceMatcher(None, p, "".join(sorted(N))).ratio())
    return 0.6 + 0.4 * r if r >= 0.88 else 0.0


def _needs_confirm(e):
    dom = e["entity_id"].split(".", 1)[0]
    if dom in NEEDS_CONFIRM_DOMAINS or e.get("device_class") in ("garage", "gate", "door"):
        return True
    return bool(set(tokens(e.get("name") or "")) & NEEDS_CONFIRM_WORDS)


def _view(e, s):
    return {"entity_id": e["entity_id"], "name": e.get("name"), "area": e.get("area"), "score": round(s, 3)}


CMD_WORDS = {"pause", "paused", "unpause", "resume", "play", "playing", "skip", "next", "previous", "prev", "back", "track", "song", "songs", "item", "replay", "last", "go", "music",
             "brightness", "color", "colour", "temperature", "up", "down", "dim", "brighten", "start", "stop", "dock", "return", "base", "to", "this", "off", "out"}
LEAD_VERBS = {"switch", "turn", "set", "toggle", "flip", "dim", "make", "change", "start", "stop", "open", "close"}


def resolve(phrase, registry, tool=None, domains=None):
    """Like _resolve, but if nothing matches and the model copied a verb into the name ("switch upstairs hallway light"), retry without it."""
    r = _resolve(phrase, registry, tool, domains)
    if r["status"] == "none":
        w = str(phrase or "").split()
        if len(w) > 1 and w[0].lower() in LEAD_VERBS:
            r2 = _resolve(" ".join(w[1:]), registry, tool, domains)
            if r2["status"] == "ok":
                return r2
    if r["status"] == "none" and phrase:
        # command words the model copied into the name ("TV pause", "item on TV", "brightness bedroom lamp"), unless a device really has that word in its name
        own = set()
        for e in registry:
            own |= set(tokens(e.get("name") or "")) | set(tokens(e.get("area") or ""))
        words = str(phrase).split()
        kept = [w for w in words if not (w.lower().strip(".,!?") in CMD_WORDS and tokens(w) and tokens(w)[0] not in own)]
        if kept and len(kept) < len(words):
            r3 = _resolve(" ".join(kept), registry, tool, domains)
            if r3["status"] == "ok":
                return r3
    return r


def _resolve(phrase, registry, tool=None, domains=None):
    """-> {"status": ok|held|none, "entity_ids": [...], "candidates": [...], "confirm": bool, "group": bool, "message": str}"""
    allowed = set(domains) if domains else set(TOOL_DOMAINS.get(tool, set()))
    pool = [e for e in registry if not allowed or e["entity_id"].split(".", 1)[0] in allowed]
    P = tokens(phrase or "")
    res = {"status": "none", "entity_ids": [], "candidates": [], "confirm": False, "group": False, "message": ""}
    plural = any(t.endswith("s") and _sing(t) in TYPE for t in re.findall(r"[a-z0-9]+", str(phrase or "").lower())) or \
        bool(re.search(r"\b(all|every)\b", str(phrase or "").lower()))
    if P:
        strong = [t for t in P if t in TYPE and t not in WEAK_TYPE]      # "garage lamp" is a lamp, not a garage door
        weak_only = all(t in TYPE for t in P)                           # "garage" alone may really mean the garage door
        tdoms = {TYPE[t][0] for t in (strong or ([t for t in P if t in TYPE] if weak_only else []))}
        if tdoms and any(_entity_score(P, e)[1] for e in pool):     # a device literally called that ("living room lights switch") wins over the type word
            tdoms = set()
        if tdoms:
            sub = [e for e in pool if e["entity_id"].split(".", 1)[0] in tdoms]
            if sub:
                pool = sub
    if not pool:
        res["message"] = "I don't have any matching devices."
        return res
    if P and plural and tdoms:
        # "kitchen lights" = the whole room: the non-type words are exactly an area name, no device is literally called that, 2+ devices in that area
        nt = {t for t in P if t not in TYPE}
        if nt and not any(_entity_score(P, e)[1] for e in pool):
            mem = [e for e in pool if e.get("area") and set(tokens(e["area"])) == nt]
            if len(mem) >= 2:
                res.update(status="ok", entity_ids=[e["entity_id"] for e in mem], group=True,
                           candidates=[_view(e, 0.95) for e in mem], confirm=any(_needs_confirm(e) for e in mem))
                return res
    if not P:
        # no name given (e.g. "set the temperature to 21"): fine only when there is exactly one such device
        if len(pool) == 1:
            e = pool[0]
            res.update(status="ok", entity_ids=[e["entity_id"]], candidates=[_view(e, 1.0)], confirm=_needs_confirm(e))
            return res
        res.update(status="held", candidates=[_view(e, 0.5) for e in pool[:4]])
        res["message"] = "Which one: " + " or ".join(_label(e) for e in pool[:4]) + "?"
        return res
    if len(pool) == 1 and all(t in TYPE for t in P):
        # "the vacuum" when the house has exactly one vacuum (whatever it is called)
        e = pool[0]
        res.update(status="ok", entity_ids=[e["entity_id"]], candidates=[_view(e, 1.0)], confirm=_needs_confirm(e))
        return res
    sc = sorted(((_entity_score(P, e), e) for e in pool), key=lambda x: -x[0][0])
    (top, top_exact), e0 = sc[0]
    near = [(s, e) for (s, _x), e in sc if top - s < MARGIN and s >= OK_MIN]
    if top < HELD_MIN:
        res["message"] = f"I couldn't find a device called \"{phrase}\"."
        res["candidates"] = [_view(e, s) for (s, _x), e in sc[:3] if s >= 0.3]
        return res
    # several equally good matches in the same room, plural wording ("kitchen lights") -> all of them
    n_exact = sum(1 for (_s, ex), _e in sc if ex)
    if plural and n_exact != 1 and len(near) >= 2 and len({(e.get("area"), e["entity_id"].split(".")[0]) for _s, e in near}) == 1:
        res.update(status="ok", entity_ids=[e["entity_id"] for _s, e in near], group=True,
                   candidates=[_view(e, s) for s, e in near], confirm=any(_needs_confirm(e) for _s, e in near))
        return res
    second = sc[1][0][0] if len(sc) > 1 else 0.0
    second_exact = sc[1][0][1] if len(sc) > 1 else False
    if top >= OK_MIN and (top - second >= MARGIN or (top_exact and not second_exact)):
        res.update(status="ok", entity_ids=[e0["entity_id"]], candidates=[_view(e0, top)], confirm=_needs_confirm(e0))
        return res
    close = [(s, e) for (s, _x), e in sc if top - s < 0.2 and s >= HELD_MIN][:4]
    res.update(status="held", candidates=[_view(e, s) for s, e in close])
    res["message"] = "Which one: " + " or ".join(_label(e) for _s, e in close) + "?"
    return res


def _label(e):
    return e.get("name") or e["entity_id"]


def choose(candidates, answer, registry):
    """After a 'held' result: resolve the user's short answer ('the ceiling one') among those candidates only."""
    ids = {c["entity_id"] for c in candidates}
    return resolve(answer, [e for e in registry if e["entity_id"] in ids])


# --------------------------------------------------------------------------- model call -> Home Assistant service call
def to_service(intent, entity_ids, args=None):
    """Returns {"domain","service","data"} or None when the action is not enabled (locks in v1)."""
    args = args or {}
    doms = {i.split(".", 1)[0] for i in entity_ids}
    if len(doms) != 1:
        return None
    d = next(iter(doms))
    if d == "lock":                                    # verified in HA core (intent/__init__.py): HassTurnOn on a lock = LOCK, HassTurnOff = UNLOCK
        if intent in ("HassTurnOn", "HassTurnOff"):
            return {"domain": "lock", "service": "lock" if intent == "HassTurnOn" else "unlock", "data": {"entity_id": list(entity_ids)}}
        return None
    if d in NEEDS_CONFIRM_DOMAINS:
        return None
    data = {"entity_id": list(entity_ids)}
    on_off = {
        "cover": ("open_cover", "close_cover"), "vacuum": ("start", "return_to_base"),   # vacuum turn-off mapping: assumption, verify in HA
    }
    if intent in ("HassTurnOn", "HassTurnOff"):
        i = 0 if intent == "HassTurnOn" else 1
        svc = on_off[d][i] if d in on_off else ("turn_on", "turn_off")[i]
        return {"domain": d, "service": svc, "data": data}
    if intent == "HassToggle":
        return {"domain": "homeassistant", "service": "toggle", "data": data}
    if intent == "HassLightSet":
        if "brightness" in args:
            data["brightness_pct"] = max(0, min(100, int(args["brightness"])))
        if args.get("color"):
            data["color_name"] = str(args["color"]).lower().replace(" ", "")
        return {"domain": "light", "service": "turn_on", "data": data}
    if intent == "HassClimateSetTemperature":
        return {"domain": "climate", "service": "set_temperature", "data": {**data, "temperature": args.get("temperature")}}
    if intent == "HassHumidifierSetpoint":
        return {"domain": "humidifier", "service": "set_humidity", "data": {**data, "humidity": args.get("humidity")}}
    if intent == "HassListAddItem":
        return {"domain": "todo", "service": "add_item", "data": {**data, "item": args.get("item")}}
    simple = {"HassVacuumStart": ("vacuum", "start"), "HassVacuumReturnToBase": ("vacuum", "return_to_base"),
              "HassMediaPause": ("media_player", "media_pause"), "HassMediaUnpause": ("media_player", "media_play"),
              "HassMediaNext": ("media_player", "media_next_track"), "HassMediaPrevious": ("media_player", "media_previous_track")}
    if intent in simple:
        return {"domain": simple[intent][0], "service": simple[intent][1], "data": data}
    return None


CSS_COLORS = set("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgrey darkgreen darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray grey green greenyellow honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgrey lightgreen lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen""".split())


def _bad_args(c):
    """Model arguments that cannot be right (a color that is not a color, a brightness that is not a number) -> message, else None."""
    a = c.get("arguments") or {}
    if c["name"] == "HassLightSet":
        col = a.get("color")
        if col is not None and str(col).lower().replace(" ", "").replace("-", "") not in CSS_COLORS:
            return f'I don\'t know the color "{col}".'
        b = a.get("brightness")
        if b is not None and not (isinstance(b, (int, float)) and 0 <= b <= 100):
            return f"Brightness {b} is not between 0 and 100."
    return None


def resolve_calls(calls, registry):
    """calls = what the model returned: [{"name": "HassTurnOff", "arguments": {"name": "kitchen light"}}, ...]
    -> {"status": ok|held|none, "actions": [per-call dict], "message": str}.
    Status is "ok" only when every call resolved; the caller may still run the "ok" actions and ask about the rest."""
    actions, msgs = [], []
    for c in calls:
        a = c.get("arguments") or {}
        bad = _bad_args(c)
        if bad:
            actions.append({"intent": c["name"], "phrase": a.get("name"), "status": "held", "entity_ids": [], "candidates": [], "confirm": False, "service": None})
            msgs.append(bad)
            continue
        r = resolve(a.get("name") or "", registry, tool=c["name"])
        act = {"intent": c["name"], "phrase": a.get("name"), "status": r["status"], "entity_ids": r["entity_ids"],
               "candidates": r["candidates"], "confirm": r["confirm"], "service": None}
        if r["status"] == "ok":
            act["service"] = to_service(c["name"], r["entity_ids"], {k: v for k, v in a.items() if k != "name"})
        else:
            msgs.append(r["message"])
        actions.append(act)
    sts = {a["status"] for a in actions}
    status = "held" if "held" in sts else ("none" if "none" in sts else "ok")
    return {"status": status, "actions": actions, "message": " ".join(msgs)}
