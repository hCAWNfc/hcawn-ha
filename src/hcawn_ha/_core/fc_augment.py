#!/usr/bin/env python3
# ==========================================================================
# fc_augment.py - automatska augmentacija FORME upita za function calling SFT.
# Menja samo kako je upit napisan (navodnici, "Please", "subject: X, body: Y"); zlatni odgovor ostaje isti.
# Transformacija se primeni SAMO ako svaka string vrednost iz zlatnog odgovora koja je bila u originalnom upitu
# ostaje u novom upitu (inace se vraca original), pa trening ne dobija neslozene primere.
#   python fc_augment.py check fc_mobile_train.jsonl [p]      # statistika + primeri, bez treninga
# ==========================================================================
import re, sys, json, random

_SQ = re.compile(r"(?<![A-Za-z0-9])'(.+?)'(?![A-Za-z0-9])")      # apostrof u reci (I've) nije navodnik
_DQ = re.compile(r'"(.+?)"')
_PLEASE = re.compile(r"^(?:could you |can you |would you )?please,?\s+", re.I)


def has_quotes(q):
    return bool(_SQ.search(q) or _DQ.search(q))


def strip_quotes(q):
    return _SQ.sub(r"\1", _DQ.sub(r"\1", q))


def strip_please(q):
    m = _PLEASE.match(q)
    if not m:
        return q
    rest = q[m.end():]
    return rest[:1].upper() + rest[1:]


def colon_form(q):
    """'... with the subject X and the body Y' -> '... subject: X, body: Y' (ocekuje vec skinute navodnike)."""
    q = re.sub(r"\b(?:with|and)? ?the subject (?:line )?(?:should be |is |of )?", " subject: ", q, flags=re.I)
    q = re.sub(r"\s*(?:,|\.)?\s*(?:and )?(?:with )?the (?:message )?(?:body|message|text)(?: of the (?:email|message))? (?:should be |is |of |reading )?",
               ", body: ", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


def saying_form(q):
    """Samo za EVAL (nije u treningu): '... subject X and the body Y' -> '... subject X saying Y'."""
    q = strip_quotes(q)
    return re.sub(r"\s+(?:and )?(?:with )?the (?:message )?(?:body|message|text)(?: of the (?:email|message))? (?:should be |is |of |reading )?",
                  " saying ", q, flags=re.I)


def dropthe_form(q):
    """'with the subject X and the body Y' -> 'with subject X and body Y'."""
    return re.sub(r"\bthe (subject|body|message|text)\b", r"\1", strip_quotes(q), flags=re.I)


def emailverb_form(q):
    """'Please send an email to X ...' -> 'Email X ...'."""
    return re.sub(r"\b(?:could you |can you |would you )?(?:please )?send (?:an? )?e-?mail to\b", "Email", strip_quotes(q), flags=re.I).replace("  ", " ")


def droptitled_form(q):
    """'event titled X' -> 'event X'."""
    return re.sub(r"\b(?:titled|called|named|entitled)\s+", "", strip_quotes(q), flags=re.I)


def semicolon_form(q):
    """'... subject: X; Body: Y'."""
    c = colon_form(strip_quotes(q))
    return c.replace(" subject:", " Subject:").replace(", body:", "; Body:")


def lowerfirst_form(q):
    """Samo za EVAL: prvo slovo malo ('send an email ...')."""
    return q[:1].lower() + q[1:]


def hey_form(q):
    """Samo za EVAL: 'Hey, send an email ...'."""
    return "Hey, " + lowerfirst_form(q)


TRAIN_TRANSFORMS = [            # (ime, tezina, funkcija)
    ("noquote", 0.25, strip_quotes),
    ("noq_noplz", 0.12, lambda q: strip_please(strip_quotes(q))),
    ("colon", 0.12, lambda q: colon_form(strip_quotes(q))),
    ("noplease", 0.08, strip_please),
    ("dropthe", 0.15, dropthe_form),
    ("emailverb", 0.10, emailverb_form),
    ("droptitled", 0.10, droptitled_form),
    ("semicolon", 0.08, semicolon_form),
]


def str_leaves(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from str_leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from str_leaves(v)


def safe(old_q, new_q, calls):
    """Svaka zlatna string vrednost koja je bila u starom upitu mora ostati u novom."""
    return all(s in new_q for s in str_leaves([c.get("arguments", {}) for c in calls]) if s and s in old_q)


def augment(ex, rng, p=0.6):
    """Vraca (primer, ime_transformacije ili None)."""
    if rng.random() >= p:
        return ex, None
    q = str(ex["query"])
    names = [t for t in TRAIN_TRANSFORMS]
    w = [t[1] for t in names]
    name, _, fn = rng.choices(names, weights=w, k=1)[0]
    nq = fn(q)
    if nq == q or not safe(q, nq, ex["calls"]):
        return ex, None
    out = dict(ex); out["query"] = nq
    return out, name


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "check":
        print(__doc__); sys.exit()
    rows = [json.loads(l) for l in open(sys.argv[2], encoding="utf-8") if l.strip()]
    p = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
    rng = random.Random(0)
    cnt, changed, shown = {}, 0, 0
    for ex in rows:
        o, n = augment(ex, rng, p)
        if n:
            changed += 1; cnt[n] = cnt.get(n, 0) + 1
            if shown < 14 and n in ("dropthe", "emailverb", "droptitled", "semicolon"):
                print("PRE :", ex["query"][:170]); print("POSLE:", o["query"][:170]); print(); shown += 1
    print(f"primera {len(rows)} | izmenjeno {changed} ({changed/len(rows):.1%}) pri p={p} | po vrsti: {cnt}")
    print(f"upita sa navodnicima: {sum(has_quotes(str(r['query'])) for r in rows)}")
