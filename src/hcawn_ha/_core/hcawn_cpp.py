# hcawn_cpp.py - Python omotac oko C++ engine-a (libhcawn.so) + ograniceno generisanje za function calling.
import ctypes, os, json, numpy as np
BOS, EOS, VOC = 256, 257, 258
_here = os.path.dirname(os.path.abspath(__file__))


class CppModel:
    def __init__(self, bin_path, lib_path=None, threads=1, decode_threads=False):
        self.lib = L = ctypes.CDLL(lib_path or os.path.join(_here, "libhcawn.so"))
        L.hc_load.restype = ctypes.c_void_p; L.hc_load.argtypes = [ctypes.c_char_p]
        L.hc_new_state.restype = ctypes.c_void_p; L.hc_new_state.argtypes = [ctypes.c_void_p]
        L.hc_clone_state.restype = ctypes.c_void_p; L.hc_clone_state.argtypes = [ctypes.c_void_p]
        L.hc_free_state.argtypes = [ctypes.c_void_p]
        L.hc_step.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.hc_prefill.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        L.hc_state_bytes.argtypes = [ctypes.c_void_p]; L.hc_state_bytes.restype = ctypes.c_int
        L.hc_set_threads.argtypes = [ctypes.c_int, ctypes.c_int]
        L.hc_set_threads(int(threads), int(bool(decode_threads)))   # threads<=0 => sva jezgra
        self.m = L.hc_load(bin_path.encode())
        if not self.m:
            raise RuntimeError("ne mogu da ucitam " + bin_path)
        self._lg = np.zeros(VOC, np.float32)

    def new_state(self):
        return L_State(self, self.lib.hc_new_state(self.m))


class L_State:
    def __init__(self, cm, p):
        self.cm, self.p = cm, p

    def clone(self):
        return L_State(self.cm, self.cm.lib.hc_clone_state(self.p))

    def feed(self, ids, want_logits=True):
        """Salje bajtove; vraca logite poslednjeg (ili None)."""
        a = np.asarray(ids, dtype=np.int32)
        lg = self.cm._lg if want_logits else None
        self.cm.lib.hc_prefill(self.cm.m, self.p, a.ctypes.data, len(a), lg.ctypes.data if lg is not None else None)
        return lg

    def nbytes(self):
        return self.cm.lib.hc_state_bytes(self.p)

    def __del__(self):
        try:
            self.cm.lib.hc_free_state(self.p)
        except Exception:
            pass


def encode_prompt(text):
    return [BOS] + list(text.encode("utf-8", errors="replace"))


_MARK = list(b"\n\n### Context:")
_MARK2 = list(b"\n\n### Query:")


def split_prefix(ids):
    """Granica ispod koje je prompt isti kad su alati isti: sve do '\\n\\n### Context:' (ili '### Query:')."""
    for mk in (_MARK, _MARK2):
        n = len(mk)
        for i in range(len(ids) - n):
            if ids[i:i + n] == mk:
                return i
    return 0


class PrefixCache:
    """Pamti stanje modela posle liste alata. Isti alati => preskace prefill tog dela."""
    def __init__(self, cm, max_items=64):
        self.cm, self.d, self.max = cm, {}, max_items
        self.hits = self.miss = 0

    def start(self, ids):
        cut = split_prefix(ids)
        key = tuple(ids[:cut])
        st = self.d.get(key)
        if st is None:
            self.miss += 1
            st = self.cm.new_state(); st.feed(ids[:cut], want_logits=False)
            if len(self.d) >= self.max:
                self.d.pop(next(iter(self.d)))
            self.d[key] = st
        else:
            self.hits += 1
        s = st.clone()
        return s, ids[cut:]


GROUND_DEFAULT = "title,subject,body,to,first_name,last_name,phone_number,email"
GROUND_ENV = tuple(k for k in os.environ.get("FC_GROUND", GROUND_DEFAULT).split(",") if k)      # FC_GROUND="" iskljucuje   # npr. FC_GROUND=title,subject,body,to,first_name,last_name,phone_number,email


VETO_ENV = os.environ.get("FC_VETO", "1") == "1"      # FC_VETO=0 iskljucuje (bez okidaca nema poziva)
FIX_ENV = os.environ.get("FC_FIX", "1") == "1"      # FC_FIX=0 iskljucuje pravilo za subject (fc_fix.py)
DATES_ENV = os.environ.get("FC_DATES", "1") == "1"      # FC_DATES=0 iskljucuje                         # relativne datume racuna pravilo (fc_dates.py), ne model


def cpp_generate(cm, ids, tools, max_new=500, cache=None, typed=True, enforce_required=True, query=None, ground=(), context=None, conf=None):
    """Ograniceno (gramatika) generisanje na C++ engine-u. Isti izlaz kao constrained_generate.
    Ako ground nije zadat, uzima se iz okruzenja FC_GROUND (polja ciju vrednost mora biti doslovan deo upita; zahteva query)."""
    from .fc_constrained import FCGrammar
    ground = ground or GROUND_ENV
    from .fc_triggers import restrict
    gtools, force = restrict(tools, str(query)) if query is not None else (tools, False)
    g = FCGrammar(gtools, typed, enforce_required, query, ground)
    g.force_call = force
    if conf is not None and force:
        conf["triggered"] = [t["name"] for t in gtools]
    if cache is not None:
        st, rest = cache.start(ids)
    else:
        st, rest = cm.new_state(), ids
    pending, logits = list(rest), None
    out = []
    for _ in range(max_new):
        al = g.allowed()
        if not al:
            break
        if len(al) == 1:
            nxt = next(iter(al))
        else:
            if pending:
                logits = st.feed(pending); pending = []
            lg = logits.copy()
            m = np.full(VOC, -np.inf, np.float32); m[sorted(al)] = 0.0
            nxt = int((lg + m).argmax())
            if conf is not None:                       # verovatnoce na mestima gde je model zaista birao (ne na prisilnim bajtovima)
                ph = g.phase
                ea = np.exp(lg - lg.max()); p_full = float(ea[nxt] / ea.sum())
                idx = sorted(al); la = lg[idx]; ex = np.exp(la - la.max()); p_res = float(ex[idx.index(nxt)] / ex.sum())
                vpos = len(getattr(getattr(g, "fsm", None), "sbuf", b"")) if ph == "VAL" else 0     # koji bajt vrednosti se bira
                conf.setdefault("steps", []).append((ph, p_res, p_full, getattr(g, "cur_key", None), vpos,
                                                         chr(nxt) if nxt < 256 else "<EOS>", bytes(t for t in out[-30:] if t < 256).decode("utf-8", errors="replace")))
        if nxt == EOS:
            break
        g.advance(nxt); out.append(nxt); pending.append(nxt)
    text = bytes(t for t in out if t < 256).decode("utf-8", errors="replace")
    if conf is not None:
        conf["raw"] = text                              # izlaz modela pre pravila za datume
    if DATES_ENV and query is not None and context is not None:
        from .fc_dates import fix_dates_ex
        text, changed, applied = fix_dates_ex(text, query, context)
        if applied and conf is not None:                # datum/sat odredjuje pravilo (promenilo ga ili potvrdilo), pa neizvesnost modela oko tih cifara ne racuna
            def _rule_digit(s):
                return s[0] == "VAL" and s[3] == "datetime" and ((("d" in applied) and s[4] < 10) or (("t" in applied) and 11 <= s[4] <= 18))
            conf["steps"] = [s for s in conf.get("steps", []) if not _rule_digit(s)]
            conf["dates_fixed"] = True
    if FIX_ENV and query is not None:
        from .fc_fix import fix_subject, fix_address, fix_title
        text, ch = fix_subject(text, str(query))
        text, ch2 = fix_address(text, str(query)); ch = ch or ch2
        text, ch3 = fix_title(text, str(query)); ch = ch or ch3
        from .fc_sr import dedupe_calls                              # an exact repeat of the previous call is dropped
        text, ch5 = dedupe_calls(text); ch = ch or ch5
        from .fc_sr import fix_map_sr                                # 'najbliza apoteka na mapi' -> 'najbliza apoteka'
        text, ch6 = fix_map_sr(text); ch = ch or ch6
        from .fc_sr import fix_subject_sr, fix_phone                 # 'sa temom X i tekstom Y' and '063 555 1212'
        text, ch7 = fix_subject_sr(text, str(query)); ch = ch or ch7
        text, ch8 = fix_phone(text, str(query)); ch = ch or ch8
        if conf is not None:                                        # sr_conf_drop
            _fx = {'subject'} if ch7 else set()
            try:
                from .fc_sr import _PHONE
                _c = {m.group(0).strip() for m in _PHONE.finditer(str(query)) if sum(d.isdigit() for d in m.group(0)) >= 7}
                _pv = [a.get('arguments', {}).get('phone_number') for a in json.loads(text) if a.get('name') == 'create_contact']
                if ch8 or (len(_c) == 1 and _pv and all(v in _c for v in _pv)):    # telefon je tacno ono sto je u upitu
                    _fx.add('phone_number')
            except Exception:
                pass
            if _fx:
                conf['steps'] = [x for x in conf.get('steps', []) if not (x[0] == 'VAL' and x[3] in _fx)]
        from .fc_sr import fix_title_lead_sr, fix_map_ground         # sr_ground
        text, ch10 = fix_title_lead_sr(text, str(query)); ch = ch or ch10
        text, ch11 = fix_map_ground(text, str(query)); ch = ch or ch11
        if conf is not None and (ch10 or ch11):
            _gk = ({'title'} if ch10 else set()) | ({'query'} if ch11 else set())
            conf['steps'] = [x for x in conf.get('steps', []) if not (x[0] == 'VAL' and x[3] in _gk)]
        _before = text                                              # sr_dedupe_conf
        text, ch9 = dedupe_calls(text); ch = ch or ch9
        if ch9 and conf is not None:
            try:
                _c0 = json.loads(_before); _keep = []; _drop = set()
                for _i, _c in enumerate(_c0):
                    if _keep and _c == _keep[-1]: _drop.add(_i)
                    else: _keep.append(_c)
                _grp, _prev = [], None
                for _st in conf.get('steps', []):
                    if _st[0] == 'NAME' and _prev != 'NAME': _grp.append([])
                    if _grp: _grp[-1].append(_st)
                    _prev = _st[0]
                if len(_grp) == len(_c0):                          # grupa po pozivu; inace ne diramo
                    conf['steps'] = [_st for _i, _g in enumerate(_grp) if _i not in _drop for _st in _g]
            except Exception:
                pass
        if conf is not None and ch:
            conf["subject_fixed"] = True
    if VETO_ENV and query is not None and not force:        # nijedan okidac nije pogodjen: model ne sme sam da izvrsi poziv (npr. "Hello there" -> lampa)
        from .fc_triggers import has_trigger_tools, ENABLED as TRIG_ON
        try:
            calls = json.loads(text)
        except Exception:
            calls = []
        if TRIG_ON and calls and has_trigger_tools([t["name"] for t in tools]):
            text = "[]"
            if conf is not None:
                conf["vetoed"] = True
    return text
