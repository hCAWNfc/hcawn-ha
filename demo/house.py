"""The simulated house: device list (this is the registry the resolver sees) and a tiny state machine that applies Home Assistant service calls."""
import copy, re

# (entity_id, name, area, device_class)
DEVICES = [
    ("light.kitchen_ceiling", "Kitchen Ceiling Light", "Kitchen", None),
    ("light.kitchen_counter", "Kitchen Counter Light", "Kitchen", None),
    ("switch.coffee_machine", "Coffee Machine", "Kitchen", None),
    ("cover.kitchen_blinds", "Kitchen Blinds", "Kitchen", None),
    ("light.living_ceiling", "Living Room Ceiling Light", "Living Room", None),
    ("light.living_lamp", "Living Room Floor Lamp", "Living Room", None),
    ("cover.living_blinds", "Living Room Blinds", "Living Room", None),
    ("fan.living_fan", "Living Room Fan", "Living Room", None),
    ("media_player.living_speaker", "Living Room Speaker", "Living Room", None),
    ("climate.thermostat", "Thermostat", "Living Room", None),
    ("light.bedroom_ceiling", "Bedroom Ceiling Light", "Bedroom", None),
    ("light.bedroom_lamp", "Bedroom Lamp", "Bedroom", None),
    ("fan.bedroom_fan", "Bedroom Fan", "Bedroom", None),
    ("cover.bedroom_blinds", "Bedroom Blinds", "Bedroom", None),
    ("humidifier.bedroom", "Bedroom Humidifier", "Bedroom", None),
    ("media_player.bedroom_speaker", "Bedroom Speaker", "Bedroom", None),
    ("light.hall", "Hallway Light", "Hallway", None),
    ("lock.front_door", "Front Door", "Hallway", "door"),
    ("vacuum.robot", "Robot Vacuum", "Hallway", None),
    ("light.kids_night", "Kids Room Night Light", "Kids Room", None),
    ("light.kids_lamp", "Kids Room Lamp", "Kids Room", None),
    ("cover.garage_door", "Garage Door", "Garage", "garage"),
    ("light.garage", "Garage Light", "Garage", None),
    ("todo.shopping", "Shopping List", None, None),
]


EXTRAS = []          # devices the visitor added at run time (same tuple shape as DEVICES)
KINDS = {            # what the visitor picks -> (Home Assistant domain, device_class)
    "light": ("light", None), "plug": ("switch", None), "fan": ("fan", None), "blinds": ("cover", None),
    "garage door": ("cover", "garage"), "lock": ("lock", "door"), "thermostat": ("climate", None),
    "speaker": ("media_player", None), "humidifier": ("humidifier", None), "vacuum": ("vacuum", None)}
_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-]{1,38}$")


def registry():
    return [{"entity_id": e, "name": n, "area": a, "aliases": [], "device_class": d} for e, n, a, d in DEVICES + EXTRAS]


def _blank(dom):
    st = {"light": {"on": False, "brightness": 100, "color": None},
          "fan": {"on": False}, "switch": {"on": False},
          "cover": {"open": False}, "lock": {"locked": True},
          "media_player": {"playing": False, "track": 1},
          "climate": {"temp": 21.0}, "humidifier": {"on": False, "humidity": 45},
          "vacuum": {"status": "docked"}, "todo": {"items": ["eggs"]}}[dom].copy()
    if dom == "todo":
        st["items"] = list(st["items"])
    return st


def add_device(state, name, area, kind):
    """Add a visitor-defined device. Returns the registry entry, or raises ValueError with a message for the page."""
    name, area, kind = " ".join(str(name or "").split()), " ".join(str(area or "").split()), str(kind or "").lower()
    if kind not in KINDS:
        raise ValueError("Pick a device type.")
    if not _NAME_OK.match(name):
        raise ValueError("Use a short name with letters, numbers, spaces or dashes (2 to 39 characters).")
    if area and not _NAME_OK.match(area):
        raise ValueError("The room name should be a short plain name.")
    if len(EXTRAS) >= 12:
        raise ValueError("That is enough extra devices for a demo. Reset the house to start again.")
    if name.lower() in {n.lower() for _e, n, _a, _d in DEVICES + EXTRAS}:
        raise ValueError(f"There is already a device called {name}.")
    dom, dc = KINDS[kind]
    base = dom + "." + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "device")
    eid, i = base, 2
    while eid in {e for e, *_ in DEVICES + EXTRAS}:
        eid, i = f"{base}_{i}", i + 1
    area = next((a for _e, _n, a, _d in DEVICES + EXTRAS if a and a.lower() == area.lower()), area) or None   # reuse an existing room spelling
    EXTRAS.append((eid, name, area, dc))
    state[eid] = _blank(dom)
    return {"entity_id": eid, "name": name, "area": area, "aliases": [], "device_class": dc}


def remove_device(state, eid):
    EXTRAS[:] = [x for x in EXTRAS if x[0] != eid]
    state.pop(eid, None)


def state_text(eid, st):
    """Short human description of one device state (used for the change log)."""
    d = eid.split(".", 1)[0]
    if d == "light": return f"on, {st['brightness']}%" + (f", {st['color']}" if st.get("color") else "") if st["on"] else "off"
    if d in ("fan", "switch"): return "on" if st["on"] else "off"
    if d == "cover": return "open" if st["open"] else "closed"
    if d == "lock": return "locked" if st["locked"] else "unlocked"
    if d == "media_player": return f"playing, track {st['track']}" if st["playing"] else "paused"
    if d == "climate": return f"set to {st['temp']:g}°"
    if d == "humidifier": return f"on, {st['humidity']}%" if st["on"] else "off"
    if d == "vacuum": return st["status"]
    if d == "todo": return f"{len(st['items'])} items"
    return ""


def events(before, state, changed):
    names = {e: n for e, n, _a, _d in DEVICES + EXTRAS}
    return [{"entity_id": e, "name": names.get(e, e), "from": state_text(e, before[e]), "to": state_text(e, state[e])} for e in changed if e in before]


def initial_state():
    EXTRAS.clear()
    s = {}
    for e, _n, _a, _d in DEVICES:
        dom = e.split(".", 1)[0]
        s[e] = _blank(dom)
    s["light.kitchen_ceiling"].update(on=True)
    s["light.living_lamp"].update(on=True, brightness=60)
    s["light.hall"].update(on=True, brightness=40)
    s["light.kids_night"].update(on=True, brightness=20, color="orange")
    s["cover.living_blinds"]["open"] = True
    s["cover.kitchen_blinds"]["open"] = True
    s["media_player.living_speaker"].update(playing=True, track=3)
    return s


def apply(state, svc):
    """Apply one service call {"domain","service","data"}; returns the list of entity ids that changed."""
    dom, name, data = svc["domain"], svc["service"], svc.get("data", {})
    changed = []
    for e in data.get("entity_id", []):
        st = state.get(e)
        if st is None:
            continue
        before = copy.deepcopy(st)
        d = e.split(".", 1)[0]
        if name == "toggle":
            if d in ("light", "fan", "switch"): st["on"] = not st["on"]
            elif d == "cover": st["open"] = not st["open"]
        elif d == "light" and name == "turn_on":
            st["on"] = True
            if "brightness_pct" in data: st["brightness"] = data["brightness_pct"]; st["on"] = data["brightness_pct"] > 0
            elif st["brightness"] == 0: st["brightness"] = 100
            if data.get("color_name"): st["color"] = data["color_name"]
        elif name == "turn_on" and d in ("fan", "switch", "humidifier"): st["on"] = True
        elif name == "turn_off" and d in ("light", "fan", "switch", "humidifier"): st["on"] = False
        elif name == "open_cover": st["open"] = True
        elif name == "close_cover": st["open"] = False
        elif name == "lock": st["locked"] = True
        elif name == "unlock": st["locked"] = False
        elif name == "media_pause": st["playing"] = False
        elif name == "media_play": st["playing"] = True
        elif name == "media_next_track": st["track"] += 1
        elif name == "media_previous_track": st["track"] = max(1, st["track"] - 1)
        elif name == "start" and d == "vacuum": st["status"] = "cleaning"
        elif name == "return_to_base": st["status"] = "docked"
        elif name == "set_temperature" and data.get("temperature") is not None: st["temp"] = float(data["temperature"])
        elif name == "set_humidity" and data.get("humidity") is not None: st["humidity"] = int(data["humidity"]); st["on"] = True
        elif name == "add_item" and data.get("item"): st["items"].append(str(data["item"]))
        if st != before:
            changed.append(e)
    return changed
