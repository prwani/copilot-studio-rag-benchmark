"""Latency + answer-quality summary per ai-* label. Usage: python summarize_ai.py [--show LABEL]"""
import json, re, statistics as st, sys
from collections import defaultdict
from analyze import phases

def final_answer(r):
    best = ""
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message" and x.get("text"):
            best = x["text"]
    return best

def searches(r):
    return sum(1 for a in r["activities"] if (a.get("raw") or {}).get("name") == "DynamicPlanStepTriggered")

g = defaultdict(list)
for l in open("results.jsonl"):
    r = json.loads(l)
    if r["label"].startswith("ai-") and r["agent"] == "aisearch-opt":
        g[r["label"]].append(r)
if "--show" in sys.argv:
    lab = sys.argv[sys.argv.index("--show") + 1]
    for r in g[lab][:4] + g[lab][-2:]:
        print(r["question_index"], r["status"], round(r["total_time_ms"]), "|", final_answer(r)[:350].replace("\n", " "), "\n")
    sys.exit()
print(f"{'label':28}{'n':>3}{'p50':>6}{'p95':>6}{'empty':>6}{'avgLen':>7}{'cited':>6}{'srch/q':>7}{'idk':>5}")
for lab, rs in sorted(g.items()):
    ok = [r for r in rs if r["status"] == "ok"]
    t = sorted(r["total_time_ms"] / 1000 for r in ok)
    ans = [final_answer(r) for r in ok]
    empty = sum(1 for a in ans if len(a.strip()) < 20)
    cited = sum(1 for a in ans if re.search(r"https?://|\[\d+\]|arxiv|\d{4}\.\d{4,5}", a, re.I))
    idk = sum(1 for a in ans if re.search(r"don't know|do not know|no (relevant )?(results|information)|couldn't find|unable to", a, re.I))
    sr = st.mean(searches(r) for r in ok) if ok else 0
    print(f"{lab:28}{len(ok):>3}{st.median(t):>6.1f}{t[int(.95*(len(t)-1))]:>6.1f}{empty:>6}{st.mean(len(a) for a in ans):>7.0f}{cited:>6}{sr:>7.2f}{idk:>5}")
