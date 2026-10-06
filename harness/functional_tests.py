"""Functional tests for the 10-source agents. Runs 6 questions through run.py, then checks each answer for
expected keywords, citations, error text and non-null retrieval.
Usage: python harness/functional_tests.py --agent original|aisearch --label LABEL"""
import argparse
import json
import re
import subprocess
from pathlib import Path

H = Path(__file__).parent
TESTS = [
    ("batch_01 fact", "For the speech-recognition model that supports verbatim and non-verbatim transcription, what word error rates do it and Whisper large-v3 achieve on Earnings21 and Earnings22?",
     [["7.64"], ["13.67"], ["11.38"], ["18.53"]], True),
    ("batch_01 fact", "Which model extends Mistral from 32 to 36 transformer blocks, and what context lengths are used in its first two pre-training phases?",
     [["Moxin"], ["36"], ["2,000", "2000", "2k"], ["4,000", "4000", "4k"]], True),
    ("batch_05 fact", "What governance mechanism does the paper 'Economic DAO Governance: A Contestable Control Approach' propose?",
     [["auction"], ["contestable", "temporary"]], True),
    ("batch_10 fact", "What do Chernikov, Gannon and Krupinski study in 'Definable convolution and idempotent Keisler measures III', and what conjecture is revisited?",
     [["idempotent"], ["Newelski"]], True),
    ("cross-batch", "Which papers discuss auction-based governance and which discuss idempotent measures over definable groups?",
     [["auction"], ["idempotent", "Keisler"]], True),
    ("out of scope", "What is a good recipe for chocolate cake?",
     [["not", "couldn", "no information", "unable", "don't", "cannot", "sorry"]], False),
]
ERR = re.compile(r"something unexpected happened|SystemError|Error code", re.I)


def answer(r):
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message":
            return x.get("text", "")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    qf = H / f"functional_{a.label}.txt"
    qf.write_text("\n".join(t[1] for t in TESTS) + "\n")
    subprocess.run(["python3", str(H / "run.py"), "--agent", a.agent, "--runs", "1", "--label", a.label,
                    "--questions-file", str(qf), "--auth", "device-code"], cwd=H.parent, capture_output=True, text=True)
    recs = {}
    for line in open(H / "results.jsonl"):
        r = json.loads(line)
        if r["label"] == a.label:
            recs[r["question_index"]] = r
    failed = 0
    for i, (name, q, expect, cite) in enumerate(TESTS, 1):
        r = recs.get(i)
        if not r or r["status"] != "ok":
            print(f"FAIL [{name}] no/failed response"); failed += 1; continue
        t = answer(r)
        steps = [x["raw"]["value"] for x in r["activities"] if (x.get("raw") or {}).get("name") == "DynamicPlanStepFinished"]
        null_ret = sum((s.get("observation") or {}).get("search_result") is None for s in steps)
        probs = []
        if ERR.search(t): probs.append("error text")
        if cite and not re.search(r"https?://|\[\d+\]", t): probs.append("no citation")
        if cite and not steps: probs.append("no search step")
        if cite and null_ret: probs.append(f"{null_ret} null retrievals")
        for alts in expect:
            if not any(w.lower() in t.lower() for w in alts): probs.append("missing " + "/".join(alts))
        ok = not probs
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} [{name}] {r['total_time_ms']/1000:.1f}s searches={len(steps)} {'; '.join(probs)}")
        print("   ", t[:260].replace("\n", " "))
    print(f"\n{len(TESTS)-failed}/{len(TESTS)} passed")


if __name__ == "__main__":
    main()
