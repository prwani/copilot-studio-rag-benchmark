"""Sequential optimisation sweep on the clone: apply step -> test -> keep if p50 total improves >=5%, else revert."""
import json
import statistics as st
import subprocess
import sys
from pathlib import Path

H = Path(__file__).parent
QS = str(H / "questions_user.txt")
STEPS = sys.argv[1:] or ["semantic_search_off", "model_mini", "concise_instructions", "model_knowledge_off", "classic_orchestration"]


def sh(*a):
    print("$", " ".join(a), flush=True)
    r = subprocess.run(a, cwd=H.parent, capture_output=True, text=True)
    print(r.stdout[-1500:], r.stderr[-500:], flush=True)
    if r.returncode:
        raise SystemExit(f"failed: {a}")


def p50(label):
    sys.path.insert(0, str(H))
    from analyze import phases
    rows = [phases(json.loads(l)) for l in open(H / "results.jsonl") if (j := json.loads(l)).get("label") == label and j["agent"] == "opt" and j["status"] == "ok"]
    f = lambda k: st.median([r[k] for r in rows if r[k] is not None]) / 1000
    return {k: round(f(k), 1) for k in ("plan_s", "search_s", "gen_s", "total_s")}, len(rows)


best = p50("step-concise_instructions")[0]["total_s"]
# baseline label contains both agents; recompute for opt only is done in p50 via agent filter
kept = []
log = []
for s in STEPS:
    label = f"step-{s}"
    sh("python3", "harness/apply_step.py", "--step", s)
    sh("python3", "harness/run.py", "--agent", "opt", "--runs", "1", "--label", label, "--questions-file", QS, "--auth", "device-code")
    m, n = p50(label)
    keep = m["total_s"] <= best * 0.95
    log.append({"step": s, "n": n, **m, "kept": keep, "prev_best": best})
    if keep:
        best = m["total_s"]
        kept.append(s)
    else:
        sh("python3", "harness/apply_step.py", "--revert", s)
    (H / "sweep_log.json").write_text(json.dumps(log, indent=1))
print("KEPT:", kept)
