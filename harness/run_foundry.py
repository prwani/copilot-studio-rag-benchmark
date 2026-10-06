"""Benchmark the Foundry prompt agent (streaming Responses API) with the same user queries.
Usage: python harness/run_foundry.py --label foundry-v3 [--question-limit N]
Per question it records phase timings from the SSE events: mcp_list_tools, reasoning, each mcp_call
(knowledge-base retrieval), time to first answer token and generation time."""
import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

H = Path(__file__).parent
BASE = "https://pw-ai-foundry-ncus.services.ai.azure.com/api/projects/proj-default"
AGENT = "orange-agent-xd5kzq7smb"
URL = f"{BASE}/agents/{AGENT}/endpoint/protocols/openai/responses?api-version=v1"


def token():
    return subprocess.check_output(
        "az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv", shell=True, text=True).strip()


def agent_version():
    req = urllib.request.Request(f"{BASE}/agents/{AGENT}?api-version=v1", headers={"Authorization": "Bearer " + token()})
    return json.load(urllib.request.urlopen(req))["versions"]["latest"]["version"]


def ask(question, tok):
    req = urllib.request.Request(URL, data=json.dumps({"input": question, "stream": True}).encode(),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    t0 = time.time()
    rec = {"question": question, "status": "ok", "error": None, "mcp_calls": [], "list_tools_s": None,
           "reasoning_s": 0.0, "first_token_s": None, "answer": "", "usage": None}
    started = {}
    ev = None
    try:
        resp = urllib.request.urlopen(req, timeout=180)
        for raw in resp:
            line = raw.decode().rstrip()
            now = time.time() - t0
            if line.startswith("event:"):
                ev = line[7:]
                continue
            if not line.startswith("data:"):
                continue
            d = json.loads(line[5:])
            it = d.get("item") or {}
            if ev == "response.output_item.added":
                started[it.get("id")] = (it.get("type"), now)
            elif ev == "response.output_item.done":
                typ, t_start = started.get(it.get("id"), (it.get("type"), now))
                dur = now - t_start
                if typ == "mcp_list_tools":
                    rec["list_tools_s"] = dur
                elif typ == "reasoning":
                    rec["reasoning_s"] += dur
                elif typ == "mcp_call":
                    out = it.get("output") or ""
                    rec["mcp_calls"].append({"start_s": t_start, "duration_s": dur, "args": it.get("arguments", "")[:300],
                                             "output_chars": len(out), "error": it.get("error")})
            elif ev == "response.output_text.delta":
                if rec["first_token_s"] is None:
                    rec["first_token_s"] = now
                rec["answer"] += d.get("delta", "")
            elif ev == "response.completed":
                rec["usage"] = d["response"].get("usage")
            elif ev in ("response.failed", "error"):
                rec["status"], rec["error"] = "failed", json.dumps(d)[:400]
    except urllib.error.HTTPError as e:
        rec["status"], rec["error"] = "failed", f"{e.code} {e.read().decode()[:300]}"
    rec["total_s"] = time.time() - t0
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--question-limit", type=int)
    ap.add_argument("--questions-file", default=str(H / "questions_user.txt"))
    a = ap.parse_args()
    qs = [l.strip() for l in open(a.questions_file) if l.strip()]
    if a.question_limit:
        qs = qs[:a.question_limit]
    ver = agent_version()
    out = H / "foundry_results.jsonl"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for i, q in enumerate(qs, 1):
        tok = token()
        r = ask(q, tok)
        r.update(label=a.label, question_index=i, agent_version=ver, started_at=started_at)
        with out.open("a") as f:
            f.write(json.dumps(r) + "\n")
        print(f"q{i}/{len(qs)} {r['status']} total={r['total_s']:.1f}s calls={len(r['mcp_calls'])}", flush=True)


if __name__ == "__main__":
    main()
