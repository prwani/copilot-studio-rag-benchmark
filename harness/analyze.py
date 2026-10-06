"""Per-phase latency breakdown from results.jsonl. Usage: python analyze.py [--label L] [--batch B]"""
import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path


def phases(rec):
    plan = tool = first_tok = msg = None
    for a in rec.get("activities", []):
        if a.get("phase") != "turn":
            continue
        x = a.get("raw") or {}
        t = a["arrival_ms"]
        if x.get("type") == "event" and x.get("name") == "DynamicPlanReceived" and plan is None:
            plan = t
        if x.get("type") == "event" and x.get("name") == "DynamicPlanStepTriggered" and tool is None:
            tool = t
        if x.get("type") == "typing" and x.get("text") and not x["text"].startswith("Searching") and first_tok is None:
            first_tok = t
        if x.get("type") == "message" and msg is None:
            msg = t
    total = rec.get("total_time_ms")
    return {
        "plan_s": plan, "search_s": (first_tok - tool) if first_tok and tool else None,
        "gen_s": (total - first_tok) if first_tok and total else None, "total_s": total,
    }


def pct(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    ap.add_argument("--file", default=str(Path(__file__).with_name("results.jsonl")))
    a = ap.parse_args()
    groups = defaultdict(list)
    for line in open(a.file):
        r = json.loads(line)
        if r.get("status") != "ok" or (a.label and r["label"] != a.label):
            continue
        groups[(r["label"], r["agent"])].append(phases(r))
    print("label/agent  n | plan p50 | search(retrieval) p50/p95 | answer-gen p50 | total p50/p95 (seconds)")
    for (lab, ag), rows in sorted(groups.items()):
        f = lambda k, p: (pct([r[k] for r in rows], p) or 0) / 1000
        print(f"{lab}/{ag} n={len(rows)} | {f('plan_s', .5):.1f} | {f('search_s', .5):.1f}/{f('search_s', .95):.1f} | "
              f"{f('gen_s', .5):.1f} | {f('total_s', .5):.1f}/{f('total_s', .95):.1f}")


if __name__ == "__main__":
    main()
