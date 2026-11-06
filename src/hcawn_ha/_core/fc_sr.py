#!/usr/bin/env python3
# ==========================================================================
# fc_sr.py - Serbian (Latin script) support for the two rule layers of hCAWN-FC:
#   1. fold(q)          : lower-case + removes diacritics (s,c,c,z,dj), so "pošalji" and "posalji" are the same text
#   2. SR_TRIGGERS      : Serbian keyword triggers, appended to fc_triggers.TRIGGERS (patterns are written in folded ASCII)
#   3. norm_for_dates(q): rewrites Serbian date/time words into the English forms that fc_dates.py already understands
#                         (sutra -> tomorrow, u 15h -> 15:00, 15. maja 2026. -> May 15 2026, u 3 popodne -> 3pm ...).
#                         It is used ONLY inside the date rule; the model still sees the original Serbian query.
#   python fc_sr.py check eval_sr.jsonl     # no model: do the triggers and the date rule reproduce the gold answers?
# ==========================================================================
import re, sys, json, os

_MAP = str.maketrans({"š": "s", "č": "c", "ć": "c", "ž": "z", "Š": "s", "Č": "c", "Ć": "c", "Ž": "z"})


def fold(q):
    return str(q).translate(_MAP).replace("đ", "dj").replace("Đ", "dj").lower()


I = re.I
SR_TRIGGERS = {
    "send_email": [r"\be-?mejl\w*", r"\bimejl\w*", r"\bmejl\w*", r"\be-?posta\w*", r"\bposalji\b.{0,30}\bporuk\w*", r"\bporuk\w*", r"\bnaslov\w*", r"\btem[aeu]\b",
                   r"\btekst\w*", r"\bsadrzaj\w*", r"\bposta\b"],
    "create_calendar_event": [r"\bkalendar\w*", r"\bzakaz\w*", r"\bpodset\w*", r"\bsastan\w*", r"\btermin\w*", r"\bdogadj\w*", r"\bdogad\w*", r"\brezerv\w*", r"\bobaveznost\w*"],
    "create_contact": [r"\bkontakt\w*", r"\bimenik\w*", r"\btelefon\w*", r"\bbroj\b", r"\bsacuvaj\w*", r"\bsnimi\b", r"\bupisi\b.{0,40}\bbroj\b"],
    "show_map": [r"\bmap[aeiu]\b", r"\bgde je\b", r"\bgde se nalazi\b", r"\bnadji\b", r"\bpokazi\b", r"\bprikazi\b", r"\bnavigac\w*", r"\blokacij\w*", r"\badres[aeu]\b",
                 r"\bnajbliz\w*", r"\bvodi me\b", r"\bpravac\w*", r"\bruta\b", r"\bput do\b", r"\bkako (?:da )?(?:stignem|dodjem)\b"],
    "open_wifi_settings": [r"\bwi-?fi\b", r"\bbezicn\w*", r"\binternet\w*\s+podesavanj\w*", r"\bpodesavanj\w*\s+(?:za\s+)?internet\w*", r"\bmrez[aeu]\b.{0,20}\bpodesavanj\w*"],
    "turn_on_flashlight": [r"\blamp\w*", r"\bsvetiljk\w*", r"\bbaterijsk\w*", r"\bblic\w*", r"\bsvetl\w*", r"\bfenjer\w*"],
    "turn_off_flashlight": [r"\blamp\w*", r"\bsvetiljk\w*", r"\bbaterijsk\w*", r"\bblic\w*", r"\bsvetl\w*", r"\bfenjer\w*"],
}
SR_RX = {k: [re.compile(p, I) for p in v] for k, v in SR_TRIGGERS.items()}


def sr_matched(query, names):
    f = fold(query)
    return [n for n in names if n in SR_RX and any(r.search(f) for r in SR_RX[n])]


# ------------------------------------------------------------------ dates and times
WDAYS = {"ponedeljak": "monday", "ponedeljka": "monday", "utorak": "tuesday", "utorka": "tuesday", "sreda": "wednesday", "sredu": "wednesday", "srede": "wednesday",
         "cetvrtak": "thursday", "cetvrtka": "thursday", "petak": "friday", "petka": "friday", "subota": "saturday", "subotu": "saturday", "subote": "saturday",
         "nedelja": "sunday", "nedelju": "sunday", "nedelje": "sunday"}
MONS = {"januara": 1, "februara": 2, "marta": 3, "aprila": 4, "maja": 5, "juna": 6, "jula": 7, "avgusta": 8, "septembra": 9, "oktobra": 10, "novembra": 11, "decembra": 12,
        "januar": 1, "februar": 2, "mart": 3, "april": 4, "maj": 5, "jun": 6, "jul": 7, "avgust": 8, "septembar": 9, "oktobar": 10, "novembar": 11, "decembar": 12}
MON_EN = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NUMW = {"jedan": 1, "jednu": 1, "dva": 2, "dve": 2, "tri": 3, "cetiri": 4, "pet": 5, "sest": 6, "sedam": 7, "osam": 8, "devet": 9, "deset": 10}
_WD = "|".join(sorted(WDAYS, key=len, reverse=True))
_MON = "|".join(sorted(MONS, key=len, reverse=True))


def _hm(h, m, ap=None):
    h, m = int(h), int(m or 0)
    if ap:
        return f"{h}{':%02d' % m if m else ''}{ap}"
    return f"{h:02d}:{m:02d}"


def norm_for_dates(query):
    """Serbian date/time words -> English forms understood by fc_dates.py. Returns the (folded) text."""
    q = fold(query)
    # explicit dates: "15. maja 2026." / "15. maj" / "15.05.2026." / "15.5."
    q = re.sub(rf"\b(\d{{1,2}})\.?\s*({_MON})\b\.?(?:\s*(\d{{4}})\b\.?)?",
               lambda m: f"{MON_EN[MONS[m.group(2)]]} {int(m.group(1))}" + (f", {m.group(3)}" if m.group(3) else ""), q)
    q = re.sub(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\.?", lambda m: f"{MON_EN[int(m.group(2))] if 1 <= int(m.group(2)) <= 12 else 'x'} {int(m.group(1))}, {m.group(3)}", q)
    # times (before the weekday words, which can contain digits-free text only)
    q = re.sub(r"\bu\s+(\d{1,2})[:.](\d{2})\s*(?:h|casova|cas)?\b(?!\.\d)", lambda m: " " + _hm(m.group(1), m.group(2)) + " ", q)
    q = re.sub(r"\bu\s+(\d{1,2})\s*(?:h|casova|casa|cas)\b", lambda m: " " + _hm(m.group(1), 0) + " ", q)
    q = re.sub(r"\bu\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:popodne|posle podne|uvece|uvece)\b", lambda m: " " + _hm(m.group(1), m.group(2), "pm") + " ", q)
    q = re.sub(r"\bu\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:ujutru|ujutro|izjutra|pre podne)\b", lambda m: " " + _hm(m.group(1), m.group(2), "am") + " ", q)
    # relative days
    q = re.sub(r"\bprekosutra\b", "day after tomorrow", q)
    q = re.sub(r"\bsutra(?:dan)?\b", "tomorrow", q)
    q = re.sub(r"\b(?:danas|veceras|jutros|noras)\b", "today", q)
    # "za 3 dana" / "za nedelju dana" / "za 2 nedelje"
    q = re.sub(r"\bza\s+nedelju dana\b", "in 7 days", q)

    def _in(m):
        n = int(m.group(1)) if m.group(1).isdigit() else NUMW.get(m.group(1))
        if n is None:
            return m.group(0)
        return f"in {n} {'weeks' if m.group(2).startswith('nedelj') else 'days'}"
    q = re.sub(r"\bza\s+(\d+|" + "|".join(NUMW) + r")\s+(dan\w*|nedelj\w*)\b", _in, q)
    # weekdays (with next / this)
    q = re.sub(rf"\b(?:sledec\w*|naredn\w*|iduc\w*|dolazec\w*)\s+({_WD})\b", lambda m: "next " + WDAYS[m.group(1)], q)
    q = re.sub(rf"\b(?:ovaj|ovog|ovu|ove)\s+({_WD})\b", lambda m: "this " + WDAYS[m.group(1)], q)
    q = re.sub(rf"\b({_WD})\b", lambda m: WDAYS[m.group(1)], q)
    return q


# ------------------------------------------------------------------ post-model rules
_FOLD1 = str.maketrans({"š": "s", "č": "c", "ć": "c", "ž": "z", "đ": "d", "Š": "S", "Č": "C", "Ć": "C", "Ž": "Z", "Đ": "D"})   # keeps the length, so indexes stay valid
_WDN = "|".join(sorted(WDAYS, key=len, reverse=True))
_MONN = "|".join(sorted(MONS, key=len, reverse=True))
_SR_ITEM = (r"(?:(?:sledec\w*|naredn\w*|ovaj|ovog|ovu|iduc\w*)\s+)?(?:prekosutra|sutra(?:dan)?|danas|veceras|jutros|" + _WDN + r")"
            r"|\d{1,2}(?:[:.]\d{2})?\s*(?:h|casova|casa|cas|popodne|posle podne|uvece|ujutru|ujutro)\b"
            r"|\d{1,2}[:.]\d{2}\b"
            r"|\d{1,2}\.\s*(?:" + _MONN + r")\b\.?(?:\s*\d{4}\.?)?"
            r"|\d{1,2}\.\s?\d{1,2}\.(?:\s?\d{4}\.?)?")
_SR_TAIL = re.compile(r"[\s,;:.\-]*(?:\b(?:za|u|na|do|od|oko)\s+)*(?:" + _SR_ITEM + r")[\s.!]*$", re.I)


def strip_title_dt_sr(title):
    """'Trening prekosutra' -> 'Trening', 'Sastanak za 1. februar 2024. u 16h' -> 'Sastanak' (the date is already in `datetime`). Never returns an empty title."""
    t = title
    for _ in range(4):
        m = _SR_TAIL.search(t.translate(_FOLD1))
        if not m or m.start() < 2:
            break
        t = t[:m.start()].rstrip(" ,;:-")
    return t if t else title


def dedupe_calls(out):
    """Removes a call that is an exact repeat of the previous one (the model sometimes repeats itself). Returns (text, changed)."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    if not isinstance(calls, list):
        return out, False
    keep = []
    for c in calls:
        if keep and c == keep[-1]:
            continue
        keep.append(c)
    if len(keep) == len(calls):
        return out, False
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":")), True


_SR_MAPTAIL = re.compile(r"[\s,;:.\-?!]*\b(?:(?:pokazi|prikazi|otvori)\s+(?:mi\s+)?)?(?:na\s+(?:google\s+)?(?:mapi|mapu|karti)|(?:mapu|mapi))\b(?:[\s,.!?]*(?:odmah|hvala|molim(?:\s+te)?|:\)|:d))*[\s.!?]*$", re.I)


def fix_map_sr(out):
    """show_map: 'najbliza apoteka na mapi' -> 'najbliza apoteka', 'Zeleni venac? Otvori mapu' -> 'Zeleni venac'. Never returns an empty query. Returns (text, changed)."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    changed = False
    for c in calls if isinstance(calls, list) else []:
        a = c.get("arguments") if isinstance(c, dict) else None
        if a and c.get("name") == "show_map" and isinstance(a.get("query"), str):
            q = a["query"]
            m = _SR_MAPTAIL.search(q.translate(_FOLD1))
            if m and m.start() >= 2:
                a["query"] = q[:m.start()].rstrip(" ,;:-?!"); changed = True
    return (json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True) if changed else (out, False)


_SR_SUBJ_TAIL = re.compile(r"[\s,;:.\-]*(?:\b(?:i|a|sa|uz|plus|pa)\s+)?\b(?:tekst\w*|poruk\w*|sadrzaj\w*|telo|telom)\s*$", re.I)


def fix_subject_sr(out, query):
    """send_email: 'sa temom Sastanak i tekstom Kasnim...' -> subject 'Sastanak' (the model keeps the connector 'i tekstom' in the subject).
    Also removes a body that the model appended to the subject. Only when the body really stands in the request. Returns (text, changed)."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    changed = False
    for c in calls if isinstance(calls, list) else []:
        a = c.get("arguments") if isinstance(c, dict) else None
        if not a or c.get("name") != "send_email":
            continue
        s, b = a.get("subject"), a.get("body")
        if not isinstance(s, str) or not isinstance(b, str) or not b or b not in query:
            continue
        ns = s
        if ns.endswith(b) and len(ns) > len(b):                      # 'Ponuda i tekstom Hvala na odgovoru' with body 'Hvala na odgovoru'
            ns = ns[:-len(b)].rstrip(" ,;:-")
        m = _SR_SUBJ_TAIL.search(ns.translate(_FOLD1))
        if m and m.start() > 0:
            ns = ns[:m.start()].rstrip(" ,;:-")
        if ns and ns != s:
            a["subject"] = ns; changed = True
    return (json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True) if changed else (out, False)


_PHONE = re.compile(r"\+?\d[\d ()\-]{5,}\d")


def fix_phone(out, query):
    """create_contact: the model wrote only a part of the number ('555 1212' for '063 555 1212'). If the request has exactly one phone-like
    number and the model's value is a shorter piece of it, the whole number is written. Returns (text, changed)."""
    cands = {m.group(0).strip() for m in _PHONE.finditer(query) if sum(ch.isdigit() for ch in m.group(0)) >= 7}
    if len(cands) != 1:
        return out, False
    full = next(iter(cands))
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    changed = False
    for c in calls if isinstance(calls, list) else []:
        a = c.get("arguments") if isinstance(c, dict) else None
        v = (a or {}).get("phone_number")
        if a and c.get("name") == "create_contact" and isinstance(v, str) and v and v != full and v in full:
            a["phone_number"] = full; changed = True
    return (json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True) if changed else (out, False)


# ------------------------------------------------------------------ check (no model)
def cmd_check(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import datetime as dt
    import fc_dates
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    trig_ok = trig_n = date_ok = date_n = 0
    bad = []
    for r in rows:
        names = [t["name"] for t in r["tools"]]
        gold = {c["name"] for c in r["calls"]}
        trig_n += 1
        if gold <= set(sr_matched(r["query"], names)):
            trig_ok += 1
        else:
            bad.append(("TRIGGER", r["query"], sorted(gold - set(sr_matched(r["query"], names)))))
        for c in r["calls"]:
            if c["name"] != "create_calendar_event":
                continue
            gdt = c["arguments"]["datetime"]
            wrong = json.dumps([{"name": c["name"], "arguments": {"title": c["arguments"]["title"], "datetime": "2031-03-03T03:03:03"}}])
            out, _, ap = fc_dates.fix_dates_ex(wrong, norm_for_dates(r["query"]), r["context"])
            got = json.loads(out)[0]["arguments"]["datetime"]
            date_n += 1
            if got[:16] == gdt[:16]:
                date_ok += 1
            else:
                bad.append(("DATE", r["query"], gdt, got))
    print(f"triggers: gold tool matched {trig_ok}/{trig_n}")
    print(f"date rule: gold datetime reproduced {date_ok}/{date_n}")
    for b in bad[:25]:
        print("  ", b)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "check":
        cmd_check(sys.argv[2])
    else:
        print(__doc__)


# ------------------------------------------------------------------ title with a date in front, map place the model garbled
_LEAD_ITEM = re.compile(r"^[\s,;:.\-]*(?:(?:za|u|na|do|od|oko)\s+)*(?:" + _SR_ITEM + r")(?:\s+(?:u|za|na|do|od|oko))*(?=\s|$)", re.I)


def fix_title_lead_sr(out, query):
    """'podseti me u petak u 9:30 da platim racune': the model wrote the title 'petak u 9:30 da platim'. The date/time (already in `datetime`) is
    cut from the front, a leading 'da' is dropped and, when the request simply continues after the model's words, the title is completed
    ('platim racune'). Only for a request with one create_calendar_event. Returns (text, changed)."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    if not (isinstance(calls, list) and len(calls) == 1 and calls[0].get("name") == "create_calendar_event"):
        return out, False
    a = calls[0].get("arguments") or {}
    t = a.get("title")
    if not isinstance(t, str) or not t:
        return out, False
    ft, cut = t.translate(_FOLD1), 0
    for _ in range(4):                                   # 'petak u 9:30'  ->  two items in a row
        m = _LEAD_ITEM.match(ft[cut:])
        if not m or m.end() == 0:
            break
        cut += m.end()
    if cut == 0 or not re.search(r"[a-z]{3}", ft[:cut], re.I):      # a bare '6:30' (English titles like '6:30 PM dinner') is left alone
        return out, False
    rest = t[cut:].strip(" ,;:-")
    rest = re.sub(r"^da\s+", "", rest, flags=re.I)
    if not rest:
        return out, False
    q, fq = str(query), str(query).translate(_FOLD1).lower()
    p = fq.find(rest.translate(_FOLD1).lower())
    if p >= 0:                                           # the request goes on after the model's words (cut at the end)
        tail = q[p + len(rest):]
        tail = re.sub(r"[\s,;:.!?\-]*(?:hvala|molim(?:\s+te)?|odmah|:\)|:d)?[\s.!?]*$", "", tail, flags=re.I)
        w = tail.split()
        if tail and tail[0] == " " and 1 <= len(w) <= 3 and not re.search(r"[@\d:]", tail) and len(rest.split()) <= 3:
            rest = (rest + tail).strip()
    if rest == t:
        return out, False
    a["title"] = rest
    return json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True


_MAP_LEAD = [
    (re.compile(r"^(?:hej|pa|ae|daj|hajde|ajde|molim te|moli te|mozes li|mozes|moze li|da mi|mi|da)\b[\s,]*", re.I), False),
    (re.compile(r"^(?:pokazi|prikazi|nadji|pronadji|otvori|vodi|odvedi|navigiraj)\b\s*(?:me\s+)?(?:mi\s+)?", re.I), True),
    (re.compile(r"^(?:gde\s+se\s+nalazi|gde\s+se\s+nalaze|gde\s+je|gde\s+su|lokaciju|lokacija|mapu\s+za|mapu|do|na\s+mapi)\b\s*", re.I), True),
]
_NORM_WS = re.compile(r"\s+")


def _squash(s):
    return _NORM_WS.sub(" ", re.sub(r"[^\w\s]", " ", s.translate(_FOLD1).lower())).strip()


def fix_map_ground(out, query):
    """show_map: when the model's place is NOT in the request at all ('Trg republike u Beogradu' -> 'Tree Republice, Beogradu'), the place is cut out of the
    request itself (after 'gde je', 'pokazi mi', ... and before 'na mapi', 'hvala', ...). Only for a request with exactly one call. Returns (text, changed)."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    if not (isinstance(calls, list) and len(calls) == 1 and calls[0].get("name") == "show_map"):
        return out, False
    a = calls[0].get("arguments") or {}
    mq = a.get("query")
    q = str(query).strip()
    if not isinstance(mq, str) or not mq or _squash(mq) in _squash(q):
        return out, False
    s, recognised, changed = q, False, True
    while changed:
        changed = False
        for rx, trig in _MAP_LEAD:
            m = rx.match(s.translate(_FOLD1))
            if m and m.end() > 0:
                s, changed = s[m.end():], True
                recognised = recognised or trig
    if not recognised:
        return out, False
    m = _SR_MAPTAIL.search(s.translate(_FOLD1))
    if m and m.start() >= 2:
        s = s[:m.start()]
    s = re.sub(r"[\s,;:.!?\-]*(?:hvala|molim(?:\s+te)?|odmah|:\)|:d)?[\s.!?]*$", "", s, flags=re.I).strip(" ,;:-?!")
    if len(s) < 3 or len(s.split()) > 10 or "@" in s or s.lower() == mq.lower():
        return out, False
    a["query"] = s
    return json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True
