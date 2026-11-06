#!/usr/bin/env python3
"""Build the final hcawn-ha English files from the three training sources and the two eval sources.
    python mix_ha.py            -> ha_train.jsonl, ha_eval.jsonl, mix_report.txt
Caps keep the data from being dominated by color/brightness and list-add templates. Training rows whose query also appears in an eval
file are removed. Prints the longest prompt+answer in bytes (training MAX_LEN is 2048)."""
import collections
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hcawn_ha"))
import shim                                               # hcawn-fc glue: provides fc_common
from fc_common import render_output, render_prompt

TRAIN = ["ha_en_train.jsonl", "ha_para_train.jsonl", "ha_gen_train.jsonl"]
EVAL = ["ha_gen_eval.jsonl", "ha_en_eval.jsonl"]
CAP = {"HassLightSet": 3500, "HassListAddItem": 2000}          # only for rows whose calls are ALL this tool


def load(p):
    return [json.loads(l) for l in open(os.path.join(HERE, p), encoding="utf-8") if l.strip()] if os.path.exists(os.path.join(HERE, p)) else []


def sig(r):
    return "+".join(sorted({c["name"] for c in r["calls"]})) or "none"


def main():
    rnd = random.Random(3)
    ev = []
    for p in EVAL:
        for r in load(p):
            r["src"] = p.split(".")[0]
            ev.append(r)
    evq = {r["query"].lower() for r in ev}
    tr, seen, leaked = [], set(), 0
    for p in TRAIN:
        for r in load(p):
            q = r["query"].lower()
            if q in evq:
                leaked += 1
                continue
            if q in seen:
                continue
            seen.add(q)
            r["src"] = p.split(".")[0]
            tr.append(r)
    rnd.shuffle(tr)
    used, out = collections.Counter(), []
    for r in tr:
        s = sig(r)
        if s in CAP and used[s] >= CAP[s]:
            continue
        used[s] += 1
        out.append(r)
    for name, rows in (("ha_train.jsonl", out), ("ha_eval.jsonl", ev)):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as fo:
            for r in rows:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    mx = max(len(render_prompt(r).encode()) + len(render_output(r["calls"]).encode()) for r in out + ev)
    lines = [f"train: {len(out)} rows (from {len(tr)} after dedupe; removed {leaked} that also occur in eval)",
             "  by source: " + str(dict(collections.Counter(r["src"] for r in out))),
             f"eval:  {len(ev)} rows, by source: " + str(dict(collections.Counter(r["src"] for r in ev))),
             f"longest prompt+answer: {mx} bytes (limit 2048)", "train by tool signature:"]
    for k, v in collections.Counter(sig(r) for r in out).most_common(14):
        lines.append(f"   {v:6d}  {k}")
    multi = sum(1 for r in out if len(r["calls"]) > 1)
    none = sum(1 for r in out if not r["calls"])
    lines.append(f"multi-call rows: {multi}   no-call rows: {none}")
    txt = "\n".join(lines)
    print(txt)
    open(os.path.join(HERE, "mix_report.txt"), "w").write(txt + "\n")


if __name__ == "__main__":
    main()
