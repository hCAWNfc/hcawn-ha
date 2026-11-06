#!/usr/bin/env python3
# ==========================================================================
# fc_triggers.py - regex okidaci (kao Needle "triggers"): ako upit pogodi okidac alata, model bira SAMO medju pogodjenim
# alatima i MORA da da poziv (ne moze [] ). Alati bez definisanog okidaca ostaju uvek dozvoljeni.
# Ako nijedan okidac ne pogodi, nista se ne menja (model radi sam, moze i da odbije).
#   python fc_triggers.py check fc_mobile_eval.jsonl     # bez modela: pokrivenost okidaca na tvom skupu + na varijantama upita
#   FC_TRIGGERS=0 iskljucuje (podrazumevano ukljuceno)
# ==========================================================================
import re, os, sys, json

I = re.I
TRIGGERS = {
    "send_email": [r"e-?mail", r"\bmail\b", r"[\w.+-]+@[\w-]+\.\w+", r"\bmessage\b", r"\bsubject\b", r"\bbody\b", r"\bping\b", r"\bnote\b", r"\b(?:tell|inform|notify|message|write to|let)\b.{0,60}\b(?:that|know|saying|about)\b"],
    "create_calendar_event": [r"\bcalendar\b", r"\bschedul\w*", r"\bappointments?\b", r"\bmeetings?\b", r"\bevents?\b", r"\bremind\w*", r"\bbook\b"],
    "create_contact": [r"contact", r"[\w.+-]+@[\w-]+\.\w+", r"\bphone\b", r"\bnumber\b", r"\bsave\b", r"\badd\b.{0,40}\b(?:number|phone)\b"],
    "show_map": [r"\bmaps?\b", r"\bwhere\b", r"\bdirections?\b", r"\bnavigat\w*", r"\blocat(?:e|ion)\b", r"\bshow me\b", r"\bfind\b", r"\baddress\b", r"\b(?:nearest|nearby|closest)\b", r"\broute\b"],
    "open_wifi_settings": [r"\bwi-?fi\b", r"\bwireless\b", r"\binternet settings\b", r"\bnetwork settings\b"],
    "turn_on_flashlight": [r"\bflash ?light\b", r"\btorch\b", r"\blights?\b"],
    "turn_off_flashlight": [r"\bflash ?light\b", r"\btorch\b", r"\blights?\b"],
}
RX = {k: [re.compile(p, I) for p in v] for k, v in TRIGGERS.items()}
ENABLED = os.environ.get("FC_TRIGGERS", "1") == "1"


def matched(query, names):
    """Skup imena alata (iz `names`) ciji okidac je pogodjen; [] ako nijedan."""
    from .fc_sr import sr_matched                                    # Serbian triggers (fc_sr.py)
    sr = set(sr_matched(query, names))
    return [n for n in names if n in RX and (any(r.search(query) for r in RX[n]) or n in sr)]


def has_trigger_tools(names):
    return any(n in RX for n in names)


def restrict(tools, query):
    """Vraca (alati_za_gramatiku, prisili_poziv). Ako nema pogotka, vraca originalne alate i False."""
    if not ENABLED or not query:
        return tools, False
    names = [t["name"] for t in tools]
    m = set(matched(query, names))
    if not m:
        return tools, False
    keep = [t for t in tools if t["name"] in m or t["name"] not in RX]       # alati bez okidaca ostaju
    return keep, True


def cmd_check(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from .fc_augment import has_quotes, strip_quotes, saying_form, dropthe_form, emailverb_form, droptitled_form
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    forms = {"orig": lambda q: q, "noquote": strip_quotes, "saying": saying_form, "emailverb": emailverb_form, "droptitled": droptitled_form}
    print(f"{'oblik':11}{'nema pogotka':>14}{'sam alat':>10}{'ima gold':>10}{'GOLD ISPADA':>13}   (n={len(rows)})")
    bad = []
    for fn, f in forms.items():
        none = one = hit = miss = 0
        for ex in rows:
            q = f(str(ex["query"]))
            names = [t["name"] for t in ex["tools"]]
            gold = {c["name"] for c in ex["calls"]}
            m = set(matched(q, names))
            if not m:
                none += 1; continue
            keep = {n for n in names if n in m or n not in RX}
            one += len(keep) == 1
            if gold <= keep: hit += 1
            else:
                miss += 1
                if fn == "orig" and len(bad) < 8: bad.append((q[:110], sorted(gold), sorted(keep)))
        n = len(rows)
        print(f"{fn:11}{none/n:>14.1%}{one/n:>10.1%}{hit/n:>10.1%}{miss/n:>13.1%}")
    print("\nGOLD ISPADA = okidac je pogodio neki alat, ali pravi alat nije medju pogodjenima (tu pravilo kvari).")
    for q, g, k in bad:
        print(f"\n  upit: {q}\n  zlatno: {g} | dozvoljeno: {k}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        cmd_check(sys.argv[2])
    else:
        print(__doc__)
