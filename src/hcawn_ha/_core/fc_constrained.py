#!/usr/bin/env python3
# ==========================================================================
# fc_constrained.py - OGRANICENO DEKODIRANJE za function calling (Needle-stil)
# ==========================================================================
# Model i dalje bira SVAKI bajt (argmax), ali samo medju bajtovima koje gramatika dozvoljava:
#   [{"name":"<jedno od ponudjenih imena>","arguments":{"<kljuc te funkcije>":<validan JSON>,...}},...]
# Dakle: uvek validan JSON, ime funkcije i imena polja su uvek iz ponudjenih alata,
# nema duplih polja, obavezna polja moraju biti popunjena, nema pokvarenih UTF-8 bajtova.
# Vrednosti (sta pise u stringovima/brojevima) i dalje pise SAMO model.
#
#   python fc_constrained.py check fc_mobile_eval.jsonl     # proveri da li gramatika prihvata ZLATNE odgovore
#   python fc_constrained.py fuzz                           # nasumicno hodanje: izlaz je uvek validan JSON
# ==========================================================================
import json
import random
import sys

EOS = 257
Q, BS = 0x22, 0x5C
DIG = set(range(0x30, 0x3A))
VSTART = {Q, 0x2D, 0x74, 0x66, 0x6E, 0x5B, 0x7B} | DIG          # " - t f n [ {  cifre
ESC_OK = set(b'"\\/bfnrt')


def _b(s):
    return set(s.encode() if isinstance(s, str) else s)


def hint_starts(typ):
    t = str(typ or "").strip().lower()
    if "|" in t or t.startswith("optional") or not t:
        return None
    if t in ("string", "str", "text"):
        return {Q}
    if t in ("integer", "int", "number", "float", "double", "long", "numeric"):
        return {0x2D} | DIG
    if t in ("boolean", "bool"):
        return {0x74, 0x66}
    if t.startswith(("array", "list", "tuple", "set")):
        return {0x5B}
    if t in ("object", "dict", "map") or t.startswith("dict"):
        return {0x7B}
    return None


# Zastita od petlji u slobodnom tekstu (npr. telo mejla): string vrednost se zatvara ako (a) ima >= MAX_STR bajtova ili
# (b) poslednjih LOOP_SPAN bajtova su periodicni (period <= 16). Rezultat je uvek validan JSON umesto 500 bajtova ponavljanja.
import os as _os
MAX_STR = int(_os.environ.get("FC_MAX_STR", 400))
LOOP_SPAN = int(_os.environ.get("FC_LOOP_SPAN", 40))


class JsonFSM:
    """Bajt-po-bajt automat za jednu JSON vrednost. advance() vraca False ako je vrednost vec zavrsena
    (broj na vrhu) i bajt pripada spoljnoj gramatici."""

    def __init__(self, hint=None, top_terms=b",}"):
        self.stack, self.mode, self.hint, self.first = [], "VALUE", hint, True
        self.top_terms = _b(top_terms)
        self.done = False
        self.cont, self.esc, self.is_key = 0, False, False
        self.lo, self.hi = 0x80, 0xBF
        self.lit, self.litpos, self.num = b"", 0, ""
        self.sbuf, self.stuck = bytearray(), False

    def _terms(self):
        if self.stack:
            return {0x2C, 0x5D} if self.stack[-1] == "a" else {0x2C, 0x7D}
        return self.top_terms

    def _value_done(self):
        if not self.stack:
            self.done, self.mode = True, "DONE"
        else:
            self.mode = "ARRN" if self.stack[-1] == "a" else "OBJN"

    def _close(self):
        self.stack.pop()
        self._value_done()

    def allowed(self):
        m = self.mode
        if m == "VALUE":
            return set(self.hint) & VSTART if (self.first and self.hint) else set(VSTART)
        if m == "ARR0":
            return VSTART | {0x5D}
        if m == "ARRN":
            return {0x2C, 0x5D}
        if m == "OBJ0":
            return {Q, 0x7D}
        if m == "OBJN":
            return {0x2C, 0x7D}
        if m == "OBJK":
            return {Q}
        if m == "COLON":
            return {0x3A}
        if m == "STR":
            if self.esc:
                return set(ESC_OK)
            if self.cont > 0:
                return set(range(self.lo, self.hi + 1))
            if not self.is_key and (self.stuck or len(self.sbuf) >= MAX_STR):
                return {Q}                    # zastita od petlje / predugog stringa: zatvori navodnik
            return (set(range(0x20, 0x80)) - {BS}) | set(range(0xC2, 0xF5)) | {BS}
        if m == "NUM":
            s, T = self.num, self._terms()
            if s in ("neg", "dot", "esign"):
                return set(DIG)
            if s == "e":
                return DIG | {0x2B, 0x2D}
            if s == "zero":
                return {0x2E, 0x65, 0x45} | T
            if s == "int":
                return DIG | {0x2E, 0x65, 0x45} | T
            if s == "frac":
                return DIG | {0x65, 0x45} | T
            if s == "exp":
                return DIG | T
        if m == "LIT":
            return {self.lit[self.litpos]}
        return set()

    def advance(self, b):
        m = self.mode
        if m == "VALUE" or (m == "ARR0" and b != 0x5D):
            if m == "ARR0":
                self.mode = "VALUE"
            self.first = False
            if b == Q:
                self.mode, self.is_key, self.cont, self.esc = "STR", False, 0, False
                self.sbuf, self.stuck = bytearray(), False
            elif b == 0x2D:
                self.mode, self.num = "NUM", "neg"
            elif b in DIG:
                self.mode, self.num = "NUM", ("zero" if b == 0x30 else "int")
            elif b in (0x74, 0x66, 0x6E):
                self.lit = {0x74: b"true", 0x66: b"false", 0x6E: b"null"}[b]
                self.mode, self.litpos = "LIT", 1
            elif b == 0x5B:
                self.stack.append("a"); self.mode = "ARR0"
            elif b == 0x7B:
                self.stack.append("o"); self.mode = "OBJ0"
            return True
        if m == "ARR0":                       # b == ']'
            self._close(); return True
        if m == "ARRN":
            if b == 0x2C:
                self.mode = "VALUE"
            else:
                self._close()
            return True
        if m in ("OBJ0", "OBJK"):
            if b == Q:
                self.mode, self.is_key, self.cont, self.esc = "STR", True, 0, False
                self.sbuf, self.stuck = bytearray(), False
            else:
                self._close()
            return True
        if m == "OBJN":
            if b == 0x2C:
                self.mode = "OBJK"
            else:
                self._close()
            return True
        if m == "COLON":
            self.mode = "VALUE"; return True
        if m == "STR":
            if not self.is_key and b != Q:
                self.sbuf.append(b)
                if len(self.sbuf) >= LOOP_SPAN and not self.stuck:
                    seg = bytes(self.sbuf[-LOOP_SPAN:])
                    self.stuck = any(seg[p:] == seg[:-p] for p in range(1, 17))
            if self.esc:
                self.esc = False
            elif self.cont > 0:
                self.cont -= 1
                self.lo, self.hi = 0x80, 0xBF
            elif b == BS:
                self.esc = True
            elif b == Q:
                if self.is_key:
                    self.mode = "COLON"
                else:
                    self._value_done()
            elif b >= 0xC0:
                self.cont = 3 if b >= 0xF0 else 2 if b >= 0xE0 else 1
                self.lo, self.hi = {0xE0: (0xA0, 0xBF), 0xED: (0x80, 0x9F), 0xF0: (0x90, 0xBF), 0xF4: (0x80, 0x8F)}.get(b, (0x80, 0xBF))
            return True
        if m == "NUM":
            T, s = self._terms(), self.num
            if b in T and s in ("zero", "int", "frac", "exp"):
                self._value_done()
                if self.done:
                    return False
                return self.advance(b)
            if b in DIG:
                self.num = "zero" if (s == "neg" and b == 0x30) else {"neg": "int", "dot": "frac", "e": "exp", "esign": "exp"}.get(s, s)
            elif b == 0x2E:
                self.num = "dot"
            elif b in (0x65, 0x45):
                self.num = "e"
            elif b in (0x2B, 0x2D):
                self.num = "esign"
            return True
        if m == "LIT":
            self.litpos += 1
            if self.litpos >= len(self.lit):
                self._value_done()
            return True
        return True


class GroundedString:
    """JSON string cija vrednost mora biti DOSLOVAN deo upita (spreca izmisljene brojeve/mejlove)."""

    def __init__(self, qbytes, nospace=False):
        self.q, self.mode, self.stack, self.done = qbytes, "G", [], False
        self.pos, self.k, self.opened = None, 0, False
        self.nospace = nospace                      # npr. 'to' / 'email': samo jedna rec, bez razmaka

    def allowed(self):
        if not self.opened:
            return {Q}
        a = {self.q[p + self.k] for p in self.pos if p + self.k < len(self.q)} - {Q, BS}
        if self.nospace:
            a -= {0x20, 0x09, 0x0A, 0x0D}
        if self.k >= 1:
            a.add(Q)
        return a

    def advance(self, b):
        if not self.opened:
            self.opened, self.pos = True, list(range(len(self.q)))
        elif b == Q:
            self.done = True
        else:
            self.pos = [p for p in self.pos if p + self.k < len(self.q) and self.q[p + self.k] == b]
            self.k += 1
        return True


class DatetimeString:
    """Vrednost polja 'datetime' mora biti GGGG-MM-DDTHH:MM:SS sa STVARNIM kalendarskim datumom i vremenom
    (spreca '2026-07-34', '30. februar', '2024-03-6thT09th'). Isti interfejs kao GroundedString."""

    def __init__(self):
        self.mode, self.stack, self.done = "D", [], False
        self.opened, self.sbuf = False, bytearray()

    def _maxday(self):
        import calendar
        try:
            y, m = int(self.sbuf[0:4]), int(self.sbuf[5:7])
            return calendar.monthrange(y, m)[1] if y >= 1 else 31
        except Exception:
            return 31

    def allowed(self):
        if not self.opened:
            return {Q}
        k, s = len(self.sbuf), self.sbuf
        d = lambda lo, hi: {0x30 + x for x in range(lo, hi + 1)}
        if k == 0: return d(1, 2)
        if k in (1, 2, 3): return d(0, 9)
        if k in (4, 7): return {0x2D}
        if k == 5: return d(0, 1)
        if k == 6: return d(1, 9) if s[5] == 0x30 else d(0, 2)
        if k == 8: return d(0, self._maxday() // 10)
        if k == 9:
            d1, mx = s[8] - 0x30, self._maxday()
            hi = 9 if d1 * 10 + 9 <= mx else mx - d1 * 10
            return d(1 if d1 == 0 else 0, hi)
        if k == 10: return {0x54}
        if k == 11: return d(0, 2)
        if k == 12: return d(0, 3) if s[11] == 0x32 else d(0, 9)
        if k in (13, 16): return {0x3A}
        if k in (14, 17): return d(0, 5)
        if k in (15, 18): return d(0, 9)
        return {Q}

    def advance(self, b):
        if not self.opened:
            self.opened = True
        elif b == Q:
            self.done = True
        else:
            self.sbuf.append(b)
        return True


NOSPACE_KEYS = set(k for k in _os.environ.get("FC_NOSPACE", "to,email,recipient").split(",") if k)
DT_KEYS = set(k for k in _os.environ.get("FC_DT_KEYS", "datetime").split(",") if k)     # prazno = iskljuceno


def gate(key, query):
    """Polje se sme uopste ponuditi samo ako upit moze da ga sadrzi."""
    k = key.lower()
    if "phone" in k:
        return sum(c.isdigit() for c in query) >= 5
    if "mail" in k:
        return "@" in query
    return True


def _trie(words):
    root = {}
    for w in words:
        n = root
        for ch in w.encode("utf-8"):
            n = n.setdefault(ch, {})
        n["$"] = w
    return root


class FCGrammar:
    def __init__(self, tools, typed=True, enforce_required=True, query=None, ground=()):
        self.query, self.ground = query, set(ground or ())
        self.qb = (query or "").encode("utf-8")
        self.tools = {t["name"]: t for t in tools}
        self.typed, self.enforce = typed, enforce_required
        self.name_trie = _trie(list(self.tools))
        self.phase, self.queue, self.after = "FORCED", bytearray(b"["), "CALL0"
        self.node, self.tool, self.used, self.fsm = None, None, set(), None
        self.force_call = False                           # True => prvi poziv je obavezan (nema "[]")
        self.cur_key = None                               # kljuc cija se vrednost trenutno pise (za skor pouzdanosti)

    # ------------------------------------------------------------ pomocno
    def _force(self, s, nxt):
        self.queue, self.after, self.phase = bytearray(s), nxt, "FORCED"

    def _remaining(self):
        out = []
        for k in self.tool["params"]:
            if k in self.used:
                continue
            if self.ground and self.query is not None and k in self.ground and k not in self.tool["required"] \
                    and not gate(k, self.query):
                continue
            out.append(k)
        return out

    def _req_ok(self):
        if not self.enforce:
            return True
        return all(r in self.used for r in self.tool["required"] if r in self.tool["params"])

    def _after(self):
        a = {0x2C} if self._remaining() else set()
        if self._req_ok():
            a.add(0x7D)
        return a

    def _start_name(self):
        self.phase, self.node = "NAME", self.name_trie

    def _start_key(self):
        self.phase, self.node = "KEY", _trie(self._remaining())

    # ------------------------------------------------------------ API
    def allowed(self):
        p = self.phase
        if p == "FORCED":
            return {self.queue[0]}
        if p == "CALL0":
            return {0x7B} if self.force_call else {0x7B, 0x5D}
        if p in ("NAME", "KEY"):
            a = {k for k in self.node if k != "$"}
            if "$" in self.node:
                a.add(Q)
            return a
        if p == "KEY0":
            a = {Q} if self._remaining() else set()
            if self._req_ok():
                a.add(0x7D)
            return a
        if p == "KEYQ":
            return {Q}
        if p == "VAL":
            a = self.fsm.allowed()
            if self.fsm.mode == "NUM" and not self.fsm.stack:
                a = (a - {0x2C, 0x7D}) | (a & self._after())
            return a
        if p == "AFTERVAL":
            return self._after()
        if p == "CALLN":
            return {0x2C, 0x5D}
        if p == "END":
            return {EOS}
        return set()

    def advance(self, b):
        p = self.phase
        if p == "FORCED":
            self.queue.pop(0)
            if not self.queue:
                self.phase = self.after
                if self.phase == "NAME":
                    self._start_name()
                elif self.phase == "KEY0":
                    pass
            return
        if p == "CALL0":
            if b == 0x7B:
                self._force(b'"name":"', "NAME")
            else:
                self.phase = "END"
            return
        if p == "NAME":
            if b == Q:
                self.tool, self.used = self.tools[self.node["$"]], set()
                self._force(b',"arguments":{', "KEY0")
            else:
                self.node = self.node[b]
            return
        if p in ("KEY0", "KEYQ"):
            if b == Q:
                self._start_key()
            else:                                     # '}' zatvara arguments
                self._force(b"}", "CALLN")
            return
        if p == "KEY":
            if b == Q:
                key = self.node["$"]
                self.cur_key = key
                self.used.add(key)
                hint = hint_starts(self.tool["params"][key]["type"]) if self.typed else None
                if self.ground and self.query is not None and key in self.ground and hint == {Q}:
                    self.fsm = GroundedString(self.qb, nospace=key in NOSPACE_KEYS)
                elif key in DT_KEYS and hint == {Q}:
                    self.fsm = DatetimeString()
                else:
                    self.fsm = JsonFSM(hint)
                self._force(b":", "VAL")
            else:
                self.node = self.node[b]
            return
        if p == "VAL":
            if not self.fsm.advance(b):               # broj zavrsen, bajt pripada nama
                self.phase = "AFTERVAL"
                return self.advance(b)
            if self.fsm.done:
                self.phase = "AFTERVAL"
            return
        if p == "AFTERVAL":
            if b == 0x2C:
                self.phase = "KEYQ"
            else:
                self._force(b"}", "CALLN")
            return
        if p == "CALLN":
            if b == 0x2C:
                self._force(b'{"name":"', "NAME")
            else:
                self.phase = "END"
            return
        if p == "END":
            self.phase = "DONE"


# ------------------------------------------------------------------ generisanje
def constrained_generate(model, tok, device, ids, tools, max_new=400, typed=True, enforce_required=True,
                         query=None, ground=(), chunk=False):
    """Kao pii_common.greedy_generate, ali svaki bajt mora da bude dozvoljen gramatikom.
    chunk=True: bajtovi koje gramatika vec zna (jedina dozvoljena opcija) se modelu salju ODJEDNOM
    (jedan forward za vise bajtova) - isti izlaz, manje poziva modela."""
    import torch
    g = FCGrammar(tools, typed, enforce_required, query, ground)
    eos = tok.eos_token_id
    x = torch.tensor([ids], dtype=torch.long, device=device)
    rm = (x != eos).to(torch.float32).unsqueeze(-1)
    logits, st = model(x, states=None, return_states=True, reset_mask=rm)
    out, pending = [], []

    def feed():
        nonlocal logits, st, pending
        if not pending:
            return
        xx = torch.tensor([pending], dtype=torch.long, device=device)
        logits, st = model(xx, states=st, return_states=True,
                           reset_mask=torch.ones((1, len(pending), 1), dtype=torch.float32, device=device))
        pending = []

    for _ in range(max_new):
        al = g.allowed()
        if not al:
            break
        if len(al) == 1:
            nxt = next(iter(al))
        else:
            feed()
            lg = logits[0, -1].float()
            mask = torch.full_like(lg, float("-inf"))
            mask[torch.tensor(sorted(al), device=lg.device)] = 0.0
            nxt = int((lg + mask).argmax())
        if nxt == eos:
            break
        g.advance(nxt)
        out.append(nxt)
        pending.append(nxt)
        if not chunk:
            feed()
    return bytes(t for t in out if t < 256).decode("utf-8", errors="replace")


# ------------------------------------------------------------------ provere
def accepts(ex, typed=True, enforce=True, ground=()):
    """Da li gramatika prihvata zlatni odgovor? Vraca (ok, pozicija_greske, opis)."""
    from .fc_common import render_output
    data = render_output(ex["calls"]).encode("utf-8") + bytes([0])
    g = FCGrammar(ex["tools"], typed, enforce, str(ex["query"]).strip() if ground else None, ground)
    for i, b in enumerate(data[:-1]):
        if b not in g.allowed():
            return False, i, f"bajt {chr(b)!r} nije dozvoljen (faza {g.phase}), do tada: {data[:i].decode('utf-8', 'ignore')[-70:]!r}"
        g.advance(b)
    if EOS not in g.allowed():
        return False, len(data) - 1, f"kraj nije dozvoljen (faza {g.phase})"
    return True, -1, ""


def cmd_check(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    for typed, enf, gr in ((True, True, ()), (False, True, ()), (False, False, ()),
                           (True, True, ("phone_number", "email")),
                           (True, True, ("title", "subject", "body", "to", "first_name", "last_name", "phone_number", "email"))):
        bad, shown = 0, 0
        for ex in rows:
            ok, _, why = accepts(ex, typed, enf, gr)
            if not ok:
                bad += 1
                if shown < 3 and gr:
                    print("   ", why)
                    shown += 1
        print(f"typed={typed!s:5} required={enf!s:5} ground={','.join(gr) or '-':18}: prihvata {1 - bad / len(rows):.1%} zlatnih odgovora ({bad} odbijeno od {len(rows)})")


def cmd_stats(path):
    """Za svako polje: koliko zlatnih STRING vrednosti je doslovan deo upita? (da izaberemo sta sme u 'ground')"""
    from collections import defaultdict
    st = defaultdict(lambda: [0, 0])
    for l in open(path, encoding="utf-8"):
        if not l.strip():
            continue
        ex = json.loads(l)
        q = str(ex["query"]).strip()
        for c in ex["calls"]:
            for k, v in c["arguments"].items():
                if isinstance(v, str):
                    st[k][0] += 1
                    st[k][1] += v in q
    print(f"{'polje':28} {'n':>5}  doslovno u upitu")
    for k, (n, g) in sorted(st.items(), key=lambda x: -x[1][0]):
        print(f"{k:28} {n:5d}  {g / n:6.1%}")


def cmd_fuzz(n=3000):
    from .fc_common import normalize_tool, parse_calls
    tools = [normalize_tool(t) for t in (
        {"name": "send_email", "description": "d", "parameters": {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to"]}},
        {"name": "set_alarm", "description": "d", "parameters": {"type": "object", "properties": {
            "hour": {"type": "integer"}, "on": {"type": "boolean"}, "tags": {"type": "array"}, "opt": {"type": "object"}}, "required": ["hour"]}},
        {"name": "ping", "description": "d", "parameters": {"type": "object", "properties": {}}})]
    rng = random.Random(1)
    done = ok = 0
    for _ in range(n):
        g = FCGrammar(tools)
        out = bytearray()
        for _ in range(400):
            al = g.allowed()
            if not al:
                break
            b = rng.choice(sorted(al))
            if b == EOS:
                done += 1
                break
            g.advance(b)
            out.append(b)
        else:
            continue
        try:
            calls = parse_calls(out.decode("utf-8"))
        except UnicodeDecodeError:
            calls = None
        good = calls is not None
        if good:
            for c in calls:
                t = next(x for x in tools if x["name"] == c["name"])
                good &= set(c["arguments"]) <= set(t["params"]) and all(r in c["arguments"] for r in t["required"])
        ok += good
        if not good:
            print("LOSE:", bytes(out)[:200])
            return
    print(f"fuzz: {done} zavrsenih izlaza od {n}, svi validan JSON + ispravna imena/polja: {ok == done}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "check":
        cmd_check(a[1])
    elif a and a[0] == "stats":
        cmd_stats(a[1])
    elif a and a[0] == "fuzz":
        cmd_fuzz()
    else:
        print(__doc__)
