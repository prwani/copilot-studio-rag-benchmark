"""Compare answers for the last two user queries across labels against the original-agent baseline.
Usage: python compare_answers.py [--show]"""
import json
import re
import sys
from pathlib import Path

rows = [json.loads(l) for l in open(Path(__file__).with_name("results.jsonl"))]


def answer(r):
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message":
            return x.get("text", "")
    return ""


qs = [l.strip() for l in open(Path(__file__).with_name("questions_user.txt")) if l.strip()]
targets = [len(qs) - 1, len(qs)]  # 1-based question_index of the last two
ref = {r["question_index"]: answer(r) for r in rows if r["label"] == "baseline" and r["agent"] == "original" and r["question_index"] in targets}
nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[\d+\]|\d{4}\.\d{4,5}v?\d*", "", t)))
for r in rows:
    if r["question_index"] not in targets or r["status"] != "ok":
        continue
    if r["label"] == "baseline" and r["agent"] == "original":
        continue
    a = answer(r)
    rn = nums(ref[r["question_index"]])
    cov = len(rn & nums(a)) / len(rn) if rn else 0
    cited = "sharepoint.com" in a
    print(f"{r['label']}/{r['agent']} q{r['question_index']}: ref-number coverage {cov:.0%}, cited={cited}, len={len(a)}")
    if "--show" in sys.argv:
        print(a[:1500], "\n---")
