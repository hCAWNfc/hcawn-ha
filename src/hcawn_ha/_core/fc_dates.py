#!/usr/bin/env python3
# ==========================================================================
# fc_dates.py - pravilo za relativne datume (tomorrow, Friday, next Monday, in 3 days) umesto modela.
# Uzima "sada" iz konteksta, racuna DATUM i upisuje ga u "datetime" (vreme dana ostaje ono sto je model napisao).
# Dira samo kad: upit ima TACNO jedan relativni izraz, nema eksplicitnog datuma (June 20th, 2026-02-10, 3/15...),
# i u izlazu ima tacno jedan "datetime". U svim ostalim slucajevima izlaz ostaje netaknut.
#   python fc_dates.py check fc_mobile_train.jsonl      # kalibracija konvencije na tvojim podacima (bez modela)
#   FC_DATES=1  [FC_DATE_CONV=bare=today,next=upcoming]  python fc_eval_cpp.py ...    # ukljucuje pravilo
# ==========================================================================
import os, re, sys, json, datetime as dt

WD = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
EXPLICIT = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|"
    rf"\b{MONTHS}\.?\s+\d{{1,2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTHS}\b|\b\d{{1,2}}(?:st|nd|rd|th)\b", re.I)
RE_DAYAFTER = re.compile(r"\bday after tomorrow\b", re.I)
RE_TOMORROW = re.compile(r"\btomorrow\b", re.I)
RE_TODAY = re.compile(r"\b(?:today|tonight|this (?:morning|afternoon|evening))\b", re.I)
RE_IN = re.compile(r"\bin\s+(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks)\b", re.I)
RE_WD = re.compile(r"\b(?:(this|next|coming|upcoming)\s+)?(" + "|".join(WD) + r")\b", re.I)
NOW_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")


TIME_AP = re.compile(r"(?<![\d:/-])(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", re.I)
TIME_24 = re.compile(r"(?<![\d:/-])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
TIMES_ENV = os.environ.get("FC_TIMES", "1") == "1"       # FC_TIMES=0 iskljucuje pravilo za sat ("2pm" -> 14:00:00)


def resolve_time(query):
    """'HH:MM:SS' ako upit ima TACNO jedan izraz za vreme (2pm, 2:30 PM, 14:30), inace None."""
    hits = set()
    for h, m, ap in TIME_AP.findall(query):
        h, m = int(h), int(m or 0)
        if not (1 <= h <= 12 and m < 60):
            return None
        hits.add((h % 12 + (12 if ap.lower() == "p" else 0), m))
    if not hits:
        for h, m in TIME_24.findall(query):
            hits.add((int(h), int(m)))
    if len(hits) != 1:
        return None
    h, m = next(iter(hits))
    return f"{h:02d}:{m:02d}:00"


def parse_conv(s):
    c = {"bare": "today", "next": "nextweek"}      # konvencija izmerena na Mobile Actions (98,3% slaganja)
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1); c[k.strip()] = v.strip()
    return c


CONV = parse_conv(os.environ.get("FC_DATE_CONV", ""))


# ---- eksplicitan datum u upitu ("July 10th, 2025", "10 July", "Feb 4th"): mesec i dan se uzimaju iz upita, ne od modela ----
MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
EXPLICIT_ENV = os.environ.get("FC_EXPLICIT", "1") == "1"      # FC_EXPLICIT=0 iskljucuje ovo pravilo
RE_MD = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,?\s*(\d{{4}})\b)?", re.I)
RE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTHS})\b\.?(?:\s*,?\s*(\d{{4}})\b)?", re.I)
RE_NUMDATE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")


def resolve_explicit(query):
    """(mesec, dan, godina|None) ako upit ima TACNO jedan eksplicitan datum sa imenom meseca, inace None.
    Brojcani oblici (2025-07-10, 7/10) i dva razlicita datuma u upitu => None (ne diramo)."""
    q = str(query)
    if RE_NUMDATE.search(q):
        return None
    hits = set()
    for m in RE_MD.finditer(q):
        mon, d, y = m.group(1), m.group(2), m.group(3)
        if mon.lower() == "may" and mon != "May": continue          # "may" kao glagol
        hits.add((MON[mon[:3].lower()], int(d), int(y) if y else None))
    for m in RE_DM.finditer(q):
        d, mon, y = m.group(1), m.group(2), m.group(3)
        if mon.lower() == "may" and mon != "May": continue
        hits.add((MON[mon[:3].lower()], int(d), int(y) if y else None))
    md = {(a, b) for a, b, _ in hits}
    if len(md) != 1:
        return None
    mo, d = next(iter(md))
    ys = {y for a, b, y in hits if y}
    if len(ys) > 1:
        return None
    return mo, d, (next(iter(ys)) if ys else None)


def parse_now(context):
    m = NOW_RE.search(context or "")
    if not m:
        return None
    try:
        return dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None


def resolve(query, now, conv=None):
    """Vraca (datum, klasa) ili None ako upit nema TACNO jedan relativni izraz ili ima eksplicitan datum."""
    conv = conv or CONV
    if now is None or EXPLICIT.search(query):
        return None
    hits = []
    if RE_DAYAFTER.search(query): hits.append(("dayafter", 2))
    elif RE_TOMORROW.search(query): hits.append(("tomorrow", 1))
    if RE_TODAY.search(query): hits.append(("today", 0))
    for m in RE_IN.finditer(query):
        n = int(m.group(1)) if m.group(1).isdigit() else NUM[m.group(1).lower()]
        hits.append(("in_n", n * (7 if m.group(2).lower().startswith("week") else 1)))
    wds = list(RE_WD.finditer(query))
    for m in wds:
        mod, w = (m.group(1) or "").lower(), WD.index(m.group(2).lower())
        delta = (w - now.weekday()) % 7
        if mod == "next":
            mode = conv["next"]
            strict = delta if delta else 7
            if mode == "upcoming": d = strict
            elif mode == "plus7": d = strict + 7
            elif mode == "nextweek": d = (7 - now.weekday()) + w         # ponedeljak sledece nedelje + w
            else: raise ValueError(mode)
            hits.append(("next_wd", d))
        else:
            d = delta if delta else (0 if conv["bare"] == "today" else 7)
            hits.append(("this_wd" if mod else "bare_wd", d))
    if len(hits) != 1:
        return None
    cls, d = hits[0]
    return (now + dt.timedelta(days=d)).date(), cls


def fix_dates_ex(out, query, context, conv=None):
    """Kao fix_dates, ali vraca (tekst, promenjeno, primenjeno). primenjeno: "" | "d" | "t" | "dt" =
    pravilo je ODREDILO datum (d) i/ili sat (t), cak i ako je model vec pogodio."""
    from .fc_sr import norm_for_dates, fold                         # Serbian date/time words -> the English forms below (fc_sr.py)
    _n = norm_for_dates(query)
    if _n != fold(query):
        query = _n
    now = parse_now(context)
    r = resolve(str(query), now, conv) if now is not None else None
    t = resolve_time(str(query)) if TIMES_ENV else None
    e = resolve_explicit(query) if (EXPLICIT_ENV and r is None) else None
    if r is None and t is None and e is None:
        return out, False, ""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False, ""
    slots = [(c, k) for c in calls if isinstance(c, dict) for k, v in (c.get("arguments") or {}).items()
             if k == "datetime" and isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", v)]
    if len(slots) != 1:
        return out, False, ""
    c, k = slots[0]
    old = c["arguments"][k]
    day = r[0].isoformat() if r is not None else old[:10]
    if e is not None:                                              # mesec i dan iz upita; godina iz upita, a ako je nema, ostaje modelova
        try:
            day = dt.date(e[2] or int(old[:4]), e[0], e[1]).isoformat()
        except ValueError:
            e = None
    new = day + "T" + (t or old[11:])
    ap = ("d" if (r is not None or e is not None) else "") + ("t" if t else "")
    if new == old:
        return out, False, ap
    c["arguments"][k] = new
    return json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True, ap


def fix_dates(out, query, context, conv=None):
    """Postprocesira JSON izlaz modela. Vraca (tekst, promenjeno)."""
    t, ch, _ = fix_dates_ex(out, query, context, conv)
    return t, ch


# ------------------------------------------------------------------ kalibracija
def cmd_check_time(rows):
    ok = tot = 0
    bad = []
    for ex in rows:
        dts = [v for c in ex["calls"] for k, v in c.get("arguments", {}).items()
               if k == "datetime" and isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", v)]
        t = resolve_time(str(ex["query"]))
        if len(dts) == 1 and t:
            tot += 1; ok += dts[0][11:] == t
            if dts[0][11:] != t and len(bad) < 6:
                bad.append((str(ex["query"])[:120], t, dts[0][11:]))
    print(f"\nSAT: redova sa jednim izrazom za vreme i jednim datetime: {tot} | pravilo se slaže sa zlatnim: {ok / max(1, tot):.1%} ({ok}/{tot})")
    for q, t, g in bad:
        print(f"  upit: {q}\n  pravilo: {t} | zlatno: {g}")


def cmd_check(path):
    import itertools
    from collections import defaultdict
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    cmd_check_time(rows)
    elig = []
    for ex in rows:
        now = parse_now(ex.get("context", ""))
        dts = [v for c in ex["calls"] for k, v in c.get("arguments", {}).items()
               if k == "datetime" and isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T", v)]
        if now and len(dts) == 1 and resolve(str(ex["query"]), now, parse_conv("")) is not None:
            elig.append((ex, now, dts[0][:10]))
    print(f"redova {len(rows)} | sa jednim relativnim izrazom i jednim datetime: {len(elig)}")
    if not elig:
        return
    best = None
    print(f"\n{'konvencija':34} {'tacno':>8}")
    for bare, nxt in itertools.product(("today", "plus7"), ("upcoming", "plus7", "nextweek")):
        conv = {"bare": bare, "next": nxt}
        ok = sum(resolve(str(ex["query"]), now, conv)[0].isoformat() == g for ex, now, g in elig)
        print(f"bare={bare:6} next={nxt:9} {ok / len(elig):>14.1%}  ({ok}/{len(elig)})")
        if best is None or ok > best[0]:
            best = (ok, conv)
    conv = best[1]
    by = defaultdict(lambda: [0, 0])
    bad = []
    for ex, now, g in elig:
        d, cls = resolve(str(ex["query"]), now, conv)
        by[cls][0] += 1; by[cls][1] += d.isoformat() == g
        if d.isoformat() != g and len(bad) < 6:
            bad.append((ex["query"][:110], now.strftime("%a %Y-%m-%d"), str(d), g))
    print(f"\nnajbolja: bare={conv['bare']} next={conv['next']}   ->   export FC_DATE_CONV=bare={conv['bare']},next={conv['next']}")
    print(f"{'klasa':12} {'n':>5} {'tacno':>8}")
    for k, (n, ok) in sorted(by.items(), key=lambda x: -x[1][0]):
        print(f"{k:12} {n:5d} {ok / n:8.1%}")
    for q, n, d, g in bad:
        print(f"\n  upit: {q}\n  sada: {n} | pravilo: {d} | zlatno: {g}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        cmd_check(sys.argv[2])
    else:
        print(__doc__)
