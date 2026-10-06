"""Independent-variant sweep on the PWAISearchAgent-Opt clone only.

Each variant is applied on top of the saved baseline, published, benchmarked with the 22 user queries,
then the baseline is restored. Usage: python harness/sweep_ai.py [variant ...]
"""
import copy
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

H = Path(__file__).parent
CLONE = "6e22f4db-edbf-f111-aaaf-00224835685d"
SRC = "d5ede7cc-d9bf-f111-aaaf-00224835685d"
B = "https://orgfe6514f3.crm.dynamics.com/api/data/v9.2"
QS = str(H / "questions_user.txt")
BASE_FILE = H / "backups" / "aisearch_opt_baseline.json"
ORIG_INSTR = "Use the knowledgebase to answer user questions and keep responses lower than 100 words."
USER_INSTR = ORIG_INSTR + " Also, avoid mutliple calls to knowledgebase."
SEARCH_ONCE = ("Use the knowledgebase to answer user questions and keep responses lower than 100 words. "
               "Combine all parts of the question into a single knowledge search query. "
               "Search again only if a specific part is missing from the results.")


def token():
    return subprocess.check_output(
        "az account get-access-token --resource https://orgfe6514f3.crm.dynamics.com --query accessToken -o tsv",
        shell=True, text=True).strip()


def api(method, path, body=None):
    for attempt in range(6):
        req = urllib.request.Request(
            B + path, data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": "Bearer " + token(), "Accept": "application/json",
                     "Content-Type": "application/json", "If-Match": "*"})
        try:
            raw = urllib.request.urlopen(req).read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode()
            if "1205" in msg or e.code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            raise SystemExit(f"{method} {path[:70]} {e.code} {msg[:400]}")
    raise SystemExit("retries exhausted")


def state():
    bot = api("GET", f"/bots({CLONE})?$select=configuration,publishedon")
    comps = api("GET", f"/botcomponents?$filter=_parentbotid_value%20eq%20{CLONE}&$select=botcomponentid,componenttype,data")["value"]
    gpt = next(c for c in comps if c["componenttype"] == 15)
    ks = next(c for c in comps if c["componenttype"] == 16)
    return bot, gpt, ks


def put_all(config_text, gpt_text, ks_text, gpt_id, ks_id):
    api("PATCH", f"/bots({CLONE})", {"configuration": config_text})
    api("PATCH", f"/botcomponents({gpt_id})", {"data": gpt_text})
    api("PATCH", f"/botcomponents({ks_id})", {"data": ks_text})


def publish(prev):
    api("POST", f"/bots({CLONE})/Microsoft.Dynamics.CRM.PvaPublish", {})
    for _ in range(60):
        cur = api("GET", f"/bots({CLONE})?$select=publishedon")["publishedon"]
        if cur != prev:
            time.sleep(20)
            return
        time.sleep(10)
    raise SystemExit("publish timeout")


def grounded(label_prefix):
    """True only if every search step in the guard run returned results and no answer is an error."""
    steps = bad = 0
    for line in open(H / "results.jsonl"):
        r = json.loads(line)
        if r["label"] != label_prefix:
            continue
        for a in r.get("activities", []):
            x = a.get("raw") or {}
            if x.get("name") == "DynamicPlanStepFinished":
                steps += 1
                bad += (x["value"].get("observation") or {}).get("search_result") is None
    return steps > 0 and bad == 0


def ydump(o):
    return yaml.safe_dump(o, sort_keys=False, allow_unicode=True, width=1000, default_flow_style=False)


def variants(cfg, gpt, ks):
    def mod(fn):
        c, g, k = copy.deepcopy(cfg), copy.deepcopy(gpt), copy.deepcopy(ks)
        fn(c, g, k)
        return c, g, k
    return {
        "baseline_r1": lambda: mod(lambda c, g, k: None),
        "baseline_r2": lambda: mod(lambda c, g, k: None),
        "user_current": lambda: mod(lambda c, g, k: (c["aISettings"].__setitem__("isSemanticSearchEnabled", False),
                                                     g.__setitem__("instructions", USER_INSTR))),
        "model_knowledge_on": lambda: mod(lambda c, g, k: c["aISettings"].__setitem__("useModelKnowledge", True)),
        "search_once_instruction": lambda: mod(lambda c, g, k: g.__setitem__("instructions", SEARCH_ONCE)),
        "instructions_60_words": lambda: mod(lambda c, g, k: g.__setitem__(
            "instructions", "Use the knowledgebase to answer user questions and keep responses lower than 60 words.")),
        "drop_vector_from_select": lambda: mod(lambda c, g, k: k["source"].__setitem__(
            "selectFields", [f for f in k["source"]["selectFields"] if f != "text_vector"])),
        "semantic_search_off": lambda: mod(lambda c, g, k: c["aISettings"].__setitem__("isSemanticSearchEnabled", False)),
        "user_instr_only": lambda: mod(lambda c, g, k: g.__setitem__("instructions", USER_INSTR)),
        "file_analysis_off": lambda: mod(lambda c, g, k: c["aISettings"].__setitem__("isFileAnalysisEnabled", False)),
        "moderation_lowest": lambda: mod(lambda c, g, k: c["aISettings"].__setitem__("contentModeration", "Lowest")),
        "model_mini": lambda: mod(lambda c, g, k: g["aISettings"]["model"].__setitem__("modelNameHint", "GPT41Mini")),
        "classic_orchestration": lambda: mod(lambda c, g, k: (c["settings"].__setitem__("GenerativeActionsEnabled", False),
                                                              c.pop("recognizer", None))),
    }


def main():
    bot, gpt_c, ks_c = state()
    cfg = json.loads(bot["configuration"])
    gpt = yaml.safe_load(gpt_c["data"])
    ks = yaml.safe_load(ks_c["data"])
    if not BASE_FILE.exists():
        # Baseline = original agent's settings with model knowledge off; the user's manual edits become a variant.
        cfg["aISettings"]["isSemanticSearchEnabled"] = True
        gpt["instructions"] = ORIG_INSTR
        BASE_FILE.write_text(json.dumps({"configuration": json.dumps(cfg, indent=2), "gpt": ydump(gpt), "ks": ks_c["data"]}, indent=1))
    base = json.loads(BASE_FILE.read_text())
    vs = variants(json.loads(base["configuration"]), yaml.safe_load(base["gpt"]), yaml.safe_load(base["ks"]))
    names = sys.argv[1:] or [n for n in vs if n != "moderation_lowest"]
    for name in names:
        label = f"ai-{name}"
        c, g, k = vs[name]()
        print(f"=== {name}", flush=True)
        put_all(json.dumps(c, indent=2), ydump(g), ydump(k), gpt_c["botcomponentid"], ks_c["botcomponentid"])
        prev = api("GET", f"/bots({CLONE})?$select=publishedon")["publishedon"]
        publish(prev)
        g_label = f"guard-{name}"
        subprocess.run(["python3", "harness/run.py", "--agent", "aisearch-opt", "--runs", "1", "--label", g_label,
                        "--question-limit", "2", "--questions-file", QS, "--auth", "device-code"],
                       cwd=H.parent, capture_output=True, text=True)
        if not grounded(g_label):
            print(f"SKIP {name}: guard run had null retrieval or no search steps", flush=True)
            put_all(base["configuration"], base["gpt"], base["ks"], gpt_c["botcomponentid"], ks_c["botcomponentid"])
            continue
        r = subprocess.run(["python3", "harness/run.py", "--agent", "aisearch-opt", "--runs", "1", "--label", label,
                            "--questions-file", QS, "--auth", "device-code"], cwd=H.parent, capture_output=True, text=True)
        print(r.stdout[-400:], r.stderr[-300:], flush=True)
        put_all(base["configuration"], base["gpt"], base["ks"], gpt_c["botcomponentid"], ks_c["botcomponentid"])
    prev = api("GET", f"/bots({CLONE})?$select=publishedon")["publishedon"]
    publish(prev)
    print("DONE: baseline restored and published", flush=True)


if __name__ == "__main__":
    main()
