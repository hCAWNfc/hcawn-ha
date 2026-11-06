"""python tests/resolver_checks.py   -> unit tests + a stress test on a synthetic house (live progress)."""
import random, sys, time
from hcawn_ha.resolver import resolve, resolve_calls, choose

def E(eid, name, area=None, **kw):
    return {"entity_id": eid, "name": name, "area": area, "aliases": kw.get("aliases", []), "device_class": kw.get("dc")}

HOUSE = [
    E("light.kitchen_ceiling", "Ceiling light", "Kitchen"), E("light.kitchen_counter", "Counter light", "Kitchen"),
    E("light.bedroom_lamp", "Bedside lamp", "Bedroom", aliases=["reading lamp"]), E("light.living_chandelier", "Chandelier", "Living room"),
    E("light.hall", "Hallway light", "Hallway"), E("fan.bedroom_fan", "Ceiling fan", "Bedroom"),
    E("switch.coffee", "Coffee machine", "Kitchen"), E("cover.living_blinds", "Blinds", "Living room"),
    E("cover.garage", "Garage door", "Garage", dc="garage"), E("lock.front", "Front door lock", "Hallway"),
    E("media_player.living_tv", "TV", "Living room"), E("media_player.kitchen_radio", "Radio", "Kitchen"),
    E("vacuum.roomba", "Roomba", None), E("climate.living", "Thermostat", "Living room"),
    E("humidifier.bedroom", "Humidifier", "Bedroom"), E("todo.shopping", "Shopping list"), E("todo.chores", "Chores list"),
    E("light.office_1", "Desk lamp 1", "Office"), E("light.office_2", "Desk lamp 2", "Office"),
]

def r(phrase, tool="HassTurnOn", reg=HOUSE):
    return resolve(phrase, reg, tool)

def t_ok(phrase, eid, tool="HassTurnOn", reg=HOUSE):
    x = r(phrase, tool, reg); assert x["status"] == "ok" and x["entity_ids"] == [eid], (phrase, x["status"], x["entity_ids"], x["candidates"])

def t_held(phrase, tool="HassTurnOn", reg=HOUSE):
    x = r(phrase, tool, reg); assert x["status"] == "held" and not x["entity_ids"] and x["message"], (phrase, x["status"], x["entity_ids"])

def t_none(phrase, tool="HassTurnOn", reg=HOUSE):
    x = r(phrase, tool, reg); assert x["status"] == "none", (phrase, x["status"], x["entity_ids"], x["candidates"])

def unit():
    t_ok("bedside lamp", "light.bedroom_lamp")
    t_ok("the bedroom lamp", "light.bedroom_lamp")             # area + type word
    t_ok("reading lamp", "light.bedroom_lamp")                 # alias
    t_ok("bedroom light", "light.bedroom_lamp")                # lamp/light synonym
    t_ok("chandelier in the living room", "light.living_chandelier")
    t_ok("living room chandelier", "light.living_chandelier")
    t_ok("kithcen counter light", "light.kitchen_counter")     # typo
    t_ok("counter light", "light.kitchen_counter")
    t_held("kitchen light")                                    # ceiling or counter
    t_held("light")                                            # far too vague
    t_ok("coffee machine", "switch.coffee")
    t_ok("the tv", "media_player.living_tv", "HassMediaPause")
    t_ok("radio", "media_player.kitchen_radio", "HassMediaNext")
    t_ok("roomba", "vacuum.roomba", "HassVacuumStart")
    t_ok("vacuum", "vacuum.roomba", "HassVacuumReturnToBase")  # only one vacuum
    t_ok("blinds", "cover.living_blinds")
    t_ok("shopping list", "todo.shopping", "HassListAddItem")
    t_ok("desk lamp 2", "light.office_2")                      # digits must match exactly
    t_none("garage lamp")
    t_none("swimming pool pump")
    t_none("garden light")
    x = r("kitchen lights"); assert x["status"] == "ok" and x["group"] and set(x["entity_ids"]) == {"light.kitchen_ceiling", "light.kitchen_counter"}, x
    x = r("office lamps"); assert x["status"] == "ok" and x["group"] and len(x["entity_ids"]) == 2, x
    h2 = HOUSE + [E("light.wc1", "Wine cellar light"), E("light.wc2", "Wine cellar reading lamp"), E("light.wc3", "Wine cellar ceiling light")]
    x = r("wine cellar lights", "HassTurnOn", h2); assert x["status"] == "ok" and x["entity_ids"] == ["light.wc1"] and not x["group"], x   # exact name beats "all lights"
    assert r("garage door", "HassTurnOn")["confirm"] and r("front door lock", "HassTurnOff")["confirm"]
    t_ok("", "climate.living", "HassClimateSetTemperature")    # no name, a single thermostat
    t_ok("", "humidifier.bedroom", "HassHumidifierSetpoint")
    two = HOUSE + [E("climate.bed", "Bedroom thermostat", "Bedroom")]
    t_held("", "HassClimateSetTemperature", two)
    assert not r("tv", "HassLightSet")["entity_ids"]           # tool restricts the domain
    # model output -> actions
    out = resolve_calls([{"name": "HassLightSet", "arguments": {"name": "bedside lamp", "brightness": 40}},
                         {"name": "HassMediaNext", "arguments": {"name": "radio"}},
                         {"name": "HassTurnOff", "arguments": {"name": "kitchen light"}}], HOUSE)
    assert out["status"] == "held" and out["actions"][0]["service"] == {"domain": "light", "service": "turn_on", "data": {"entity_id": ["light.bedroom_lamp"], "brightness_pct": 40}}
    assert out["actions"][1]["service"]["service"] == "media_next_track" and out["actions"][2]["service"] is None
    # clarification round trip
    h = r("kitchen light"); c = choose(h["candidates"], "the ceiling one", HOUSE)
    assert c["status"] == "ok" and c["entity_ids"] == ["light.kitchen_ceiling"], c
    lock = resolve_calls([{"name": "HassTurnOn", "arguments": {"name": "front door lock"}}], HOUSE)["actions"][0]
    assert lock["confirm"] and lock["service"]["service"] == "lock"   # HA: HassTurnOn on a lock = lock; always needs a spoken yes
    unl = resolve_calls([{"name": "HassTurnOff", "arguments": {"name": "front door lock"}}], HOUSE)["actions"][0]
    assert unl["confirm"] and unl["service"]["service"] == "unlock"
    print("unit tests: all passed")

AREAS = ["kitchen", "bedroom", "living room", "hallway", "bathroom", "office", "garage", "attic", "basement", "porch", "kids room", "dining room"]
KINDS = [("light", ["ceiling light", "floor lamp", "desk lamp", "chandelier", "reading lamp", "wall sconce"], "light"),
         ("fan", ["ceiling fan", "desk fan", "extractor fan"], "fan"), ("media_player", ["tv", "stereo", "speaker", "radio"], "media"),
         ("cover", ["blinds", "curtains", "shutters"], "cover"), ("vacuum", ["vacuum", "roomba"], "vacuum"), ("switch", ["coffee machine", "kettle", "plug"], "switch")]

def stress(n_houses=300, seed=1):
    rnd = random.Random(seed); t0 = time.time()
    stat = dict(exact_ok=0, exact_n=0, wrong_ok=0, amb_held=0, amb_n=0, noarea_ok=0, noarea_n=0, typo_ok=0, typo_held=0, typo_none=0, typo_n=0, absent_none=0, absent_n=0)
    for h in range(n_houses):
        reg, used = [], set()
        for _ in range(rnd.randint(8, 40)):
            dom, names, _c = rnd.choice(KINDS); nm = rnd.choice(names); ar = rnd.choice(AREAS)
            if (nm, ar) in used: continue
            used.add((nm, ar)); reg.append(E(f"{dom}.{ar.replace(' ', '_')}_{nm.replace(' ', '_')}", nm.capitalize(), ar.capitalize()))
        tool = {"light": "HassTurnOn", "fan": "HassTurnOn", "media_player": "HassMediaPause", "cover": "HassTurnOn", "vacuum": "HassVacuumStart", "switch": "HassTurnOn"}
        for e in reg:
            dom = e["entity_id"].split(".")[0]; tl = tool[dom]; nm, ar = e["name"].lower(), e["area"].lower()
            x = resolve(f"{ar} {nm}", reg, tl); stat["exact_n"] += 1; stat["exact_ok"] += x["entity_ids"] == [e["entity_id"]]
            if x["status"] == "ok" and x["entity_ids"] != [e["entity_id"]] and not x["group"]: stat["wrong_ok"] += 1
            # name without room: unique among same-name devices -> must be ok; otherwise must NOT silently pick one
            same = [o for o in reg if o["name"] == e["name"] and o["entity_id"].split(".")[0] == dom]
            x = resolve(nm, reg, tl)
            if len(same) == 1:
                stat["noarea_n"] += 1; stat["noarea_ok"] += x["entity_ids"] == [e["entity_id"]]
                if x["status"] == "ok" and x["entity_ids"] != [e["entity_id"]] and not x["group"]: stat["wrong_ok"] += 1
            else:
                stat["amb_n"] += 1; stat["amb_held"] += x["status"] != "ok" or x["group"]
            # typo in the room word
            if len(ar) > 5:
                i = rnd.randrange(1, len(ar) - 2); ty = ar[:i] + ar[i + 1] + ar[i] + ar[i + 2:]
                x = resolve(f"{ty} {nm}", reg, tl); stat["typo_n"] += 1
                stat["typo_ok"] += x["entity_ids"] == [e["entity_id"]]; stat["typo_held"] += x["status"] == "held"; stat["typo_none"] += x["status"] == "none"
                if x["status"] == "ok" and x["entity_ids"] != [e["entity_id"]] and not x["group"]: stat["wrong_ok"] += 1
        # a device that is not in this house
        ab = resolve("swimming pool pump", reg, "HassTurnOn"); stat["absent_n"] += 1; stat["absent_none"] += ab["status"] == "none"
        print(f"\rstress: house {h + 1}/{n_houses} | devices tested {stat['exact_n']} | wrong-ok {stat['wrong_ok']} | {time.time() - t0:.0f}s   ", end="", flush=True)
    print()
    pc = lambda a, b: f"{100 * a / max(b, 1):.1f}%"
    print(f"  room+name           ok & correct: {pc(stat['exact_ok'], stat['exact_n'])}  ({stat['exact_n']})")
    print(f"  name only, unique   ok & correct: {pc(stat['noarea_ok'], stat['noarea_n'])}  ({stat['noarea_n']})")
    print(f"  name only, ambiguous not auto-picked: {pc(stat['amb_held'], stat['amb_n'])}  ({stat['amb_n']})")
    print(f"  typo in room        correct: {pc(stat['typo_ok'], stat['typo_n'])} | asked: {pc(stat['typo_held'], stat['typo_n'])} | not found: {pc(stat['typo_none'], stat['typo_n'])}")
    print(f"  absent device       'none': {pc(stat['absent_none'], stat['absent_n'])}")
    print(f"  WRONG device chosen silently: {stat['wrong_ok']}   <- must be 0")
    return stat

if __name__ == "__main__":
    unit()
    s = stress(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
    sys.exit(1 if s["wrong_ok"] else 0)
