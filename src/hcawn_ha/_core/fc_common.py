#!/usr/bin/env python3
# ==========================================================================
# fc_common.py - zajednicko za function calling (bez torch-a, moze da se testira svuda)
#   - normalize_tool : razni formati alata -> jedan format
#   - render_prompt  : kompaktan prompt (alati + upit)
#   - render_output  : [{"name":..,"arguments":{..}}] (kompaktan JSON)
#   - parse_calls    : tekst modela -> lista poziva (ili None ako nije validan JSON)
#   - score_one      : strogo poredjenje (imena, REDOSLED i sve vrednosti argumenata)
# ==========================================================================
import json
import re

DESC_MAX = 100     # max bajtova opisa alata
PDESC_MAX = 50     # max bajtova opisa parametra

PROMPT = "### Tools:\n{tools}\n\n{context}### Query:\n{query}\n\n### Call:\n"


def _cut(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s.encode("utf-8")) <= n else s.encode("utf-8")[:n].decode("utf-8", "ignore").rstrip() + "~"


def _jl(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return x
    return x


def normalize_tool(t):
    """Vraca {"name","description","params":{pname:{"type","description","enum"}},"required":[...]} ili None."""
    t = _jl(t)
    if not isinstance(t, dict):
        return None
    if "function" in t and isinstance(t["function"], dict):
        t = t["function"]
    try:
        return _normalize(t)
    except Exception:
        return None


def _normalize(t):
    name = t.get("name")
    if not name:
        return None
    params = _jl(t.get("parameters") or t.get("arguments") or {})
    props, req = {}, []
    if isinstance(params, dict) and isinstance(params.get("properties"), dict):      # JSON schema
        props = params["properties"]
        req = params.get("required")
        req = [str(x) for x in req] if isinstance(req, list) else []
    elif isinstance(params, dict):                                                    # xLAM stil: {arg:{type,description,default}}
        props = {k: v for k, v in params.items() if isinstance(v, dict)}
        req = [k for k, v in props.items() if "default" not in v and not str(v.get("type", "")).startswith("Optional")]
    out = {}
    for k, v in props.items():
        v = v if isinstance(v, dict) else {}
        typ = v.get("type", "string")
        if isinstance(typ, list):
            typ = "|".join(map(str, typ))
        out[k] = {"type": str(typ), "description": v.get("description", ""), "enum": v.get("enum")}
    return {"name": str(name), "description": t.get("description", ""), "params": out, "required": req}


def render_tool(t):
    sig = []
    for k, v in t["params"].items():
        s = f"{k}: {v['type']}" + ("*" if k in t["required"] else "")
        if v.get("enum"):
            s += "{" + "|".join(map(str, v["enum"][:8])) + "}"
        sig.append(s)
    line = f"- {t['name']}({', '.join(sig)}): {_cut(t['description'], DESC_MAX)}"
    notes = [f"{k}={_cut(v['description'], PDESC_MAX)}" for k, v in t["params"].items() if v.get("description")]
    if notes:
        line += " [" + "; ".join(notes) + "]"
    return line


def render_prompt(ex):
    tools = "\n".join(render_tool(t) for t in ex["tools"])
    ctx = _cut(ex.get("context", ""), 400)
    ctx = f"### Context:\n{ctx}\n\n" if ctx else ""
    return PROMPT.format(tools=tools, context=ctx, query=str(ex["query"]).strip())


def render_output(calls):
    return json.dumps([{"name": c["name"], "arguments": c.get("arguments", {})} for c in calls],
                      ensure_ascii=False, separators=(",", ":"))


def parse_calls(text):
    try:
        v = json.loads(text.strip())
    except Exception:
        return None
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return None
    out = []
    for c in v:
        if not isinstance(c, dict) or "name" not in c:
            return None
        a = c.get("arguments", {})
        if not isinstance(a, dict):
            return None
        out.append({"name": c["name"], "arguments": a})
    return out


def _norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        return s
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def score_one(pred_text, gold_calls):
    """Vraca dict: valid, names (imena i redosled), exact (imena + svi argumenti, redosled bitan), exact_any_order."""
    pred = parse_calls(pred_text)
    if pred is None:
        return {"valid": 0, "names": 0, "exact": 0, "any_order": 0}
    gn = [c["name"] for c in gold_calls]
    pn = [c["name"] for c in pred]
    names = int(gn == pn)
    G = [(c["name"], _norm(c.get("arguments", {}))) for c in gold_calls]
    P = [(c["name"], _norm(c.get("arguments", {}))) for c in pred]
    exact = int(G == P)
    rest = list(G)
    ok = len(G) == len(P)
    if ok:
        for p in P:
            if p in rest:
                rest.remove(p)
            else:
                ok = False
                break
    return {"valid": 1, "names": names, "exact": exact, "any_order": int(ok)}


if __name__ == "__main__":
    ex = {"tools": [normalize_tool({"name": "set_alarm", "description": "Set an alarm",
                                    "parameters": {"type": "object", "properties": {"hour": {"type": "integer", "description": "0-23"},
                                                                                     "label": {"type": "string"}}, "required": ["hour"]}})],
          "query": "wake me at 7", "calls": [{"name": "set_alarm", "arguments": {"hour": 7}}]}
    p = render_prompt(ex)
    o = render_output(ex["calls"])
    print(p + o)
    assert score_one(o, ex["calls"])["exact"] == 1
    assert score_one('[{"name":"set_alarm","arguments":{"hour":7.0}}]', ex["calls"])["exact"] == 1
    assert score_one('[{"name":"set_alarm","arguments":{"hour":8}}]', ex["calls"])["names"] == 1
    assert score_one("nije json", ex["calls"])["valid"] == 0
    print("fc_common selftest OK")
