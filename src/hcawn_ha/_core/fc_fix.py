#!/usr/bin/env python3
# ==========================================================================
# fc_fix.py - mala pravila posle modela (kao fc_dates.py). Za sada jedno:
#   "subject Meeting and body See you at 3pm" (bez navodnika) -> model upise subject="Meeting and body".
#   Pravilo skida zavrsno " and body" / " and the body" / " with the message" ... sa subject-a,
#   ali SAMO ako je body odmah posle toga u upitu (struktura "subject S <veznik> body B").
#   FC_FIX=0 iskljucuje.   Test: python fc_fix.py test
# ==========================================================================
import re, sys, json

TAIL = re.compile(r"[\s,;:]*(?:\b(?:and|with|plus)\b\s*)?(?:\bthe\b\s*)?\b(?:body|message|text|content)\b\s*$", re.I)


EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")


def fix_address(out, query):
    """Ako upit ima TACNO jednu mejl adresu, a `to`/`email` je samo njen deo (npr. "example.com"), upisuje celu adresu."""
    addrs = set(EMAIL.findall(query))
    if len(addrs) != 1:
        return out, False
    full = next(iter(addrs))
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    changed = False
    for c in calls if isinstance(calls, list) else []:
        a = c.get("arguments") if isinstance(c, dict) else None
        for k in ("to", "email"):
            v = (a or {}).get(k)
            if isinstance(v, str) and v and v != full and v in full and (k == "to" and c.get("name") == "send_email" or k == "email" and c.get("name") == "create_contact"):
                a[k] = full; changed = True
    return (json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True) if changed else (out, False)


_WD = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_MO = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_ITEM = (r"(?:(?:next|this|coming|upcoming)\s+)?(?:day after tomorrow|tomorrow|today|tonight|" + _WD + r"|\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?|\d{1,2}:\d{2}|"
         + _MO + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)")
_TAILDT = re.compile(r"[\s,;:.\-]*(?:\b(?:on|at|for|by|from|the)\s+)*" + _ITEM + r"[\s.!]*$", re.I)


def strip_title_dt(title):
    """'Review tomorrow at 9:30 AM' -> 'Review' (skida zavrsne izraze za datum/vreme; nikad ne vraca prazan naslov)."""
    t = title
    for _ in range(4):
        m = _TAILDT.search(t)
        if not m or m.start() < 2:
            break
        t = t[:m.start()].rstrip(" ,;:-")
    return t if t and t != title else title


def fix_title(out, query):
    """create_calendar_event: naslov koji se zavrsava datumom/vremenom iz upita (a vreme/datum su vec u `datetime`) -> skida taj kraj."""
    try:
        calls = json.loads(out)
    except Exception:
        return out, False
    changed = False
    for c in calls if isinstance(calls, list) else []:
        a = c.get("arguments") if isinstance(c, dict) else None
        if not a or c.get("name") != "create_calendar_event" or not isinstance(a.get("title"), str) or not a.get("datetime"):
            continue
        n = strip_title_dt(a["title"])
        from .fc_sr import strip_title_dt_sr                        # Serbian: 'Trening prekosutra' -> 'Trening'
        n = strip_title_dt_sr(n)
        if n != a["title"]:
            a["title"] = n; changed = True
    return (json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True) if changed else (out, False)


def fix_subject(out, query):
    """Vraca (tekst, promenjeno)."""
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
        if not isinstance(s, str) or not isinstance(b, str) or not b:
            continue
        m = TAIL.search(s)
        if not m or m.start() == 0:
            continue
        ns = s[:m.start()].rstrip()
        i = query.find(s)
        if not ns or i < 0 or query.find(b, i + len(s)) < 0:     # body mora doci posle subject-a u upitu
            continue
        a["subject"] = ns; changed = True
    if not changed:
        return out, False
    return json.dumps(calls, ensure_ascii=False, separators=(",", ":")), True


def cmd_check(path):
    """Primeni pravilo na ZLATNE naslove: koliko ih menja (treba ~0%, inace pravilo kvari tacne odgovore)."""
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    n = ch = 0; bad = []
    for ex in rows:
        for c in ex["calls"]:
            t = (c.get("arguments") or {}).get("title")
            if c.get("name") == "create_calendar_event" and isinstance(t, str):
                n += 1
                if strip_title_dt(t) != t:
                    ch += 1
                    if len(bad) < 8: bad.append((t, strip_title_dt(t)))
    print(f"zlatnih naslova kalendara: {n} | pravilo bi ih promenilo: {ch} ({ch / max(1, n):.2%})")
    for a, b in bad: print(f"   {a!r} -> {b!r}")


if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "check":
    cmd_check(sys.argv[2]); sys.exit()

if __name__ == "__main__":
    T = [("Send an email to bob@example.com with subject Meeting and body See you at 3pm",
          '[{"name":"send_email","arguments":{"to":"bob@example.com","subject":"Meeting and body","body":"See you at 3pm"}}]', "Meeting"),
         ("Email bob@example.com with subject Lunch and body Are you free today",
          '[{"name":"send_email","arguments":{"to":"bob@example.com","subject":"Lunch and body","body":"Are you free today"}}]', "Lunch"),
         ("Email bob with subject Project status and the message Done",
          '[{"name":"send_email","arguments":{"to":"bob","subject":"Project status and the message","body":"Done"}}]', "Project status"),
         ("Send mail to bob with subject Body and body Hello",          # tacno ostaje
          '[{"name":"send_email","arguments":{"to":"bob","subject":"Body","body":"Hello"}}]', "Body"),
         ("Email bob with subject Meeting and body",                    # nema body-ja posle: ne dira
          '[{"name":"send_email","arguments":{"to":"bob","subject":"Meeting and body","body":"x"}}]', "Meeting and body")]
    ok = True
    for q, o, want in T:
        t, ch = fix_subject(o, q)
        got = json.loads(t)[0]["arguments"]["subject"]
        print("OK " if got == want else "GRESKA", ch, got); ok &= got == want
    for q, o, want in [("Tell bob@example.com that I will be late", '[{"name":"send_email","arguments":{"to":"example.com","body":"I will be late"}}]', "bob@example.com"),
                       ("Email Matt saying hi", '[{"name":"send_email","arguments":{"to":"Matt","body":"hi"}}]', "Matt"),
                       ("Mail a@x.com or b@y.com", '[{"name":"send_email","arguments":{"to":"x.com"}}]', "x.com")]:
        t, ch = fix_address(o, q); got = json.loads(t)[0]["arguments"]["to"]
        print("OK " if got == want else "GRESKA", ch, got); ok &= got == want
    for t, want in [("Review tomorrow at 9:30 AM", "Review"), ("Lunch with Bob on Friday", "Lunch with Bob"), ("Call at 3pm", "Call"),
                    ("Team Sync next Monday at 2pm", "Team Sync"), ("Dentist checkup", "Dentist checkup"), ("Friday", "Friday"),
                    ("Call about insurance", "Call about insurance"), ("Meet on May 5th, 2026", "Meet"), ("Pizza night 2026-11-05", "Pizza night")]:
        got = strip_title_dt(t); print("OK " if got == want else "GRESKA", repr(t), "->", repr(got)); ok &= got == want
    sys.exit(0 if ok else 1)
