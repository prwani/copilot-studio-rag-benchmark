"""Build the consolidated HTML benchmark report -> harness/final_report.html"""
import html
import json
import re
import statistics as st
import sys
from pathlib import Path

H = Path(__file__).parent
sys.path.insert(0, str(H))
from analyze import phases  # noqa: E402
from quality import IDK, score  # noqa: E402

rows = [json.loads(l) for l in open(H / "results.jsonl")]
frows = [json.loads(l) for l in open(H / "foundry_results.jsonl")]


def ans(r):
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message":
            return x.get("text", "")
    return ""


def pct(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


def steps(r):
    return sum(1 for a in r["activities"] if (a.get("raw") or {}).get("name") == "DynamicPlanStepFinished")


CITE = re.compile(r"https?://|\[\d+\]|【|cite\d")


def cs_group(label, agent):
    rs = [r for r in rows if r["label"] == label and r["agent"] == agent and r["status"] == "ok"]
    return {(r.get("run_index", 0), r["question_index"]): r for r in rs}


def metrics_cs(group, ref):
    ph = [phases(r) for r in group.values()]
    f = lambda k, p: (pct([x[k] for x in ph], p) or 0) / 1000
    answers = {q: ans(r) for q, r in group.items()}
    sc = [score(answers[k], ref[k[1]]) for k in answers if k[1] in ref]
    return dict(n=len(group), p50=f("total_s", .5), p95=f("total_s", .95), plan=f("plan_s", .5),
                search=f("search_s", .5), gen=f("gen_s", .5), sq=st.mean(steps(r) for r in group.values()),
                length=st.mean(len(a) for a in answers.values()), cited=sum(bool(CITE.search(a)) for a in answers.values()) / len(answers),
                num=st.mean(n for _, n in sc), idk=sum(bool(IDK.search(a)) for a in answers.values()))


def metrics_f(label, ref):
    g = [r for r in frows if r["label"] == label and r["status"] == "ok"]
    tot = [r["total_s"] for r in g]
    plan = [r["mcp_calls"][0]["start_s"] for r in g if r["mcp_calls"]]
    retr = [sum(c["duration_s"] for c in r["mcp_calls"]) for r in g]
    gen = [r["total_s"] - max(c["start_s"] + c["duration_s"] for c in r["mcp_calls"]) for r in g if r["mcp_calls"]]
    sc = [score(r["answer"], ref[r["question_index"]]) for r in g]
    return dict(n=len(g), p50=pct(tot, .5), p95=pct(tot, .95), plan=pct(plan, .5), search=pct(retr, .5), gen=pct(gen, .5),
                sq=st.mean(len(r["mcp_calls"]) for r in g), length=st.mean(len(r["answer"]) for r in g),
                cited=sum(bool(CITE.search(r["answer"])) for r in g) / len(g), num=st.mean(n for _, n in sc),
                idk=sum(bool(IDK.search(r["answer"])) for r in g))


AI = "aisearch-opt"
ref_ai = {k[1]: ans(r) for k, r in cs_group("ai-baseline_r1", AI).items()}
ref_sp = {k[1]: ans(r) for k, r in cs_group("baseline", "opt").items()}

# (agent type, agent, experiment, label, rowagent, ref, quality_impact, note)
spec = [
    ("Copilot Studio + SharePoint", "PWSharepointAgent (original, untouched)", "Baseline 1 (original settings)", "baseline", "original", ref_sp, "n/a", "Reference. Captured before any changes."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent (your edits)", "Baseline 2a: model knowledge off + 'avoid multiple KB calls'", "sharepoint-baseline-final", "original", ref_sp, "No", "Ran after your 22:09 IST publish, so it already included your edits."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent (your edits)", "Baseline 2b: repeat of 2a", "sharepoint-baseline2", "original", ref_sp, "No", "Repeat run; difference from 2a is run-to-run noise."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Baseline (clone)", "baseline", "opt", ref_sp, "n/a", "Clone reference for the SharePoint experiments."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Concise instructions", "step-concise_instructions", "opt", ref_sp, "Yes", "Answers about 72% shorter and fewer cited (62%)."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Semantic search off", "step-semantic_search_off", "opt", ref_sp, "No", "No latency gain."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Model knowledge off", "step-model_knowledge_off", "opt", ref_sp, "No", "No latency gain."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Classic orchestration", "step-classic_orchestration", "opt", ref_sp, "No", "No latency gain; answers shorter."),
    ("Copilot Studio + SharePoint", "PWSharepointAgent-Opt (clone)", "Model GPT-4.1 mini", "step-model_mini", "opt", ref_sp, "No", "Slower (22s) with longer answers."),
    ("Copilot Studio + AI Search", "PWAISearchAgent (original)", "Baseline (model knowledge on)", "aisearch-baseline", "aisearch", ref_ai, "n/a", "Reference for the original AI Search agent."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Baseline run 1 (model knowledge off)", "ai-baseline_r1", AI, ref_ai, "n/a", "Reference. Noise floor is about 0.35s vs run 2."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Baseline run 2", "ai-baseline_r2", AI, ref_ai, "n/a", "Repeat of run 1."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Drop text_vector from selected fields", "ai-drop_vector_from_select", AI, ref_ai, "No", "Same length, 100% cited."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "File analysis off", "ai-file_analysis_off", AI, ref_ai, "No", ""),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "'Search once' instruction", "ai-search_once_instruction", AI, ref_ai, "No", ""),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Semantic search off", "ai-semantic_search_off", AI, ref_ai, "No", "Number coverage slightly lower, within noise."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Instruction: avoid multiple KB calls", "ai-user_instr_only", AI, ref_ai, "No", ""),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Semantic off + avoid multiple KB calls", "ai-user_current", AI, ref_ai, "No", ""),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Instructions: under 60 words", "ai-instructions_60_words", AI, ref_ai, "Yes", "About 15% shorter, less detail (by design)."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Model knowledge on", "ai-model_knowledge_on", AI, ref_ai, "Yes", "5 of 22 answers had no citation (ungrounded)."),
    ("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Model GPT-4.1 mini", "ai-model_mini", AI, ref_ai, "Yes", "Numeric answers correct, but Q8 filled in from general knowledge and Q19 was incomplete."),
]

spec += [
    ("Copilot Studio + SharePoint (10 sources, ~3 GB)", "PWSharepointAgent (current)", "Baseline, 5 runs x 22 queries", "sp10-baseline", "original", ref_ai, "n/a",
     "10 SharePoint folder sources. Instructions: 100 words, model knowledge off. Contains one 141s outlier. Functional test: 3 of 6 passed (see below)."),
    ("Copilot Studio + AI Search (10 sources, ~3 GB)", "PWAISearchAgent (current)", "Baseline, 5 runs x 22 queries", "ai10-baseline", "aisearch", ref_ai, "n/a",
     "10 AI Search indexes, about 42,900 chunks. Model knowledge on. Very stable (within-question stdev 0.7s). Functional test: 3 of 6 passed (see below)."),
]
out_rows = []
for t, a, e, label, ra, ref, q, note in spec:
    m = metrics_cs(cs_group(label, ra), ref)
    out_rows.append((t, a, e, m, q, note + (" Search phase is a measurement artifact for this run (negative)." if m["search"] < 0 else "")))
for lab, name in (("foundry-v3-r1", "Run 1"), ("foundry-v3-r2", "Run 2")):
    out_rows.append(("Azure AI Foundry agent", "orange-agent-xd5kzq7smb v3 (gpt-5.6-luna, low reasoning, MCP knowledge base)",
                     f"Baseline {name}", metrics_f(lab, ref_ai), "Yes",
                     "Numeric answers correct. Q8 gave generic quantization advice instead of saying the sources lack it; source markers are not links."))
out_rows.append(("Azure AI Foundry agent", "orange-agent-xd5kzq7smb v4 (v3 + tool_choice required)", "Run 1 (tool call forced)",
                 metrics_f("foundry-v4", ref_ai), "Yes",
                 "Always calls the tool as intended and answers well on Q19, Q21, Q22. Q8 still partly ungrounded: retrieved text mentions GPTQ and QLoRA but not AWQ, SmoothQuant or INT8 PTQ, which the answer lists."))
out_rows.append(("Copilot Studio + AI Search", "PWAISearchAgent-Opt (clone)", "Classic orchestration", None, "n/a",
                 "Not measurable: classic mode emits no search steps, so the retrieval check skipped it."))

f1 = lambda v: "-" if v is None else f"{v:.1f}"


def tr(r):
    t, a, e, m, q, note = r
    qc = {"Yes": "yes", "No": "no"}.get(q, "na")
    if m is None:
        cells = "<td colspan='9' class='na'>not measured</td>"
    else:
        s = "n/m" if m["search"] < 0 else f1(m["search"])
        cells = (f"<td class='num'>{m['n']}</td><td class='num b'>{m['p50']:.1f}</td><td class='num'>{m['p95']:.1f}</td>"
                 f"<td class='num'>{f1(m['plan'])}</td><td class='num'>{s}</td><td class='num'>{f1(m['gen'])}</td>"
                 f"<td class='num'>{m['sq']:.2f}</td><td class='num'>{m['length']:.0f}</td><td class='num'>{m['cited']:.0%}</td>")
    return (f"<tr><td>{html.escape(t)}</td><td>{html.escape(a)}</td><td>{html.escape(e)}</td>{cells}"
            f"<td class='{qc}'>{q}</td><td>{html.escape(note)}</td></tr>")


def best(typ_prefix):
    c = [r for r in out_rows if r[3] and r[0].startswith(typ_prefix)]
    return min(c, key=lambda r: r[3]["p50"])


sp_base = next(r for r in out_rows if r[2].startswith("Baseline 1"))[3]
ai_base = next(r for r in out_rows if r[2] == "Baseline run 1 (model knowledge off)")[3]
fd = [r[3] for r in out_rows if r[0].startswith("Azure AI Foundry") and "v3" in r[1]]
fd4 = next(r[3] for r in out_rows if "v4" in r[1])
fd_p50 = st.mean(m["p50"] for m in fd)
fd_p95 = st.mean(m["p95"] for m in fd)
fd_plan = st.mean(m["plan"] for m in fd)
fd_ret = st.mean(m["search"] for m in fd)
fd_gen = st.mean(m["gen"] for m in fd)
mini = next(r for r in out_rows if r[2] == "Model GPT-4.1 mini" and "AI Search" in r[0])[3]

doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>RAG agent latency benchmark</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;max-width:1500px}}
h1{{margin-bottom:4px}} .sub{{color:#666;margin-bottom:20px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0 28px}}
th,td{{border:1px solid #ddd;padding:6px 8px;vertical-align:top}} th{{background:#f0f3f7;text-align:left;position:sticky;top:0}}
td.num{{text-align:right;white-space:nowrap}} td.b{{font-weight:600}}
td.yes{{background:#fde8e8;color:#a40000;font-weight:600;text-align:center}}
td.no{{background:#e6f4ea;color:#1b6e2d;font-weight:600;text-align:center}} td.na{{color:#888;text-align:center}}
.card{{display:inline-block;border:1px solid #ddd;border-radius:8px;padding:12px 18px;margin:0 12px 12px 0;min-width:230px}}
.card .v{{font-size:26px;font-weight:600}} code{{background:#f3f3f3;padding:1px 4px}}
</style></head><body>
<h1>RAG agent latency benchmark</h1>
<div class="sub">22 user queries per run, one conversation per query. All times in seconds. Generated from harness/results.jsonl and harness/foundry_results.jsonl.</div>

<h2>Headline</h2>
<div class="card"><div>Copilot Studio + SharePoint (baseline 1)</div><div class="v">{sp_base['p50']:.1f}s p50</div><div>p95 {sp_base['p95']:.1f}s</div></div>
<div class="card"><div>Copilot Studio + AI Search (clone baseline)</div><div class="v">{ai_base['p50']:.1f}s p50</div><div>p95 {ai_base['p95']:.1f}s</div></div>
<div class="card"><div>Copilot Studio + AI Search + GPT-4.1 mini</div><div class="v">{mini['p50']:.1f}s p50</div><div>p95 {mini['p95']:.1f}s (quality impact: Yes)</div></div>
<div class="card"><div>Azure AI Foundry agent v3</div><div class="v">{fd_p50:.1f}s p50</div><div>p95 {fd_p95:.1f}s (avg of 2 runs)</div></div>
<div class="card"><div>Azure AI Foundry agent v4 (tool call forced)</div><div class="v">{fd4['p50']:.1f}s p50</div><div>p95 {fd4['p95']:.1f}s (1 run)</div></div>

<ul>
<li>AI Search roughly halves latency compared with live SharePoint search ({ai_base['p50']:.1f}s vs {sp_base['p50']:.1f}s). SharePoint retrieval is slow (about 8-11s per search) and happens sequentially.</li>
<li>Foundry agent v3 averages {fd_p50:.1f}s: about {fd_plan:.1f}s before the knowledge-base call (list tools plus reasoning), about {fd_ret:.1f}s retrieval, about {fd_gen:.1f}s generation. It always makes exactly one knowledge-base call.</li>
<li>Foundry v4 (tool_choice required) is as fast as v3 ({fd4['p50']:.1f}s p50) and still makes one knowledge-base call per question. Forcing the call did not stop the model from adding general knowledge on questions the sources barely cover (Q8), so an instruction to answer only from retrieved content is the next fix to test.</li>\n<li>On Copilot Studio with AI Search, only switching to GPT-4.1 mini changed latency materially ({ai_base['p50']:.1f}s to {mini['p50']:.1f}s), and it carries a quality impact. All other settings are within the roughly 0.35s noise floor.</li>
<li>Server-side Azure AI Search query time is small (keyword about 9ms, vector about 179ms, hybrid about 375ms), so search capacity affects concurrency and SLA more than single-query latency. No provisioned search capacity was created.</li>
</ul>

<h2>All experiments</h2>
<table><thead><tr><th>Agent type</th><th>Agent</th><th>Experiment</th><th>n</th><th>p50 (s)</th><th>p95 (s)</th>
<th>Plan p50</th><th>Search p50</th><th>Generation p50</th><th>Searches / question</th><th>Avg answer chars</th><th>Answers with citation</th>
<th>Quality impact</th><th>Notes</th></tr></thead><tbody>
{''.join(tr(r) for r in out_rows)}
</tbody></table>

<h2>Functional tests before the 10-source benchmark</h2>
<p>Six questions per agent run before benchmarking (<code>harness/functional_tests.py</code>): two batch_01 facts, one batch_05 fact, one batch_10 fact, one cross-batch question and one out-of-scope question.</p>
<table><thead><tr><th>Test</th><th>SharePoint agent</th><th>AI Search agent</th></tr></thead><tbody>
<tr><td>batch_01 fact (Reverb WER numbers)</td><td class="no">PASS (19.0s)</td><td class="no">PASS (17.8s)</td></tr>
<tr><td>batch_01 fact (Moxin 7B)</td><td class="no">PASS (18.6s)</td><td class="no">PASS (8.8s)</td></tr>
<tr><td>batch_05 fact (DAO contestable control)</td><td class="yes">Found the right paper; answer did not say "auction" (strict keyword)</td><td class="yes">Found the right paper; no "auction", no citation</td></tr>
<tr><td>batch_10 fact (Keisler measures)</td><td class="yes">FAIL: empty answer. Rerun: 2 good answers and 1 "not sure how to help" fallback</td><td class="yes">Found the right paper; did not mention "Newelski"; no citation</td></tr>
<tr><td>Cross-batch question</td><td class="yes">No search step recorded; answer given</td><td class="no">PASS</td></tr>
<tr><td>Out-of-scope (chocolate cake)</td><td class="no">PASS: declined</td><td class="yes">Answered with a recipe: expected, model knowledge is on for this agent</td></tr>
</tbody></table>
<p>Takeaways: both agents retrieve from the later batches (05 and 10), so all 10 sources are reachable. The AI Search misses are strict keyword checks and its model-knowledge setting, not retrieval failures. SharePoint showed occasional empty or fallback answers in functional testing (2 of 4 attempts on one question), but none in the 110-conversation benchmark. Treat SharePoint reliability as a risk to monitor.</p>

<h2>How to read this</h2>
<ul>
<li><b>Phase split:</b> for Copilot Studio, plan is the time to the first plan event, search is from the first search step to the first answer token, and generation is the remainder. For Foundry, plan is the time before the first knowledge-base call (MCP list tools plus reasoning), search is the sum of knowledge-base call durations, and generation is the time after the last call. The two splits are close but not identical definitions. "n/m" means not meaningful (the step overlapped with answer streaming).</li>
<li><b>Quality impact</b> is a Yes/No judgment combining answer length, share of facts (numbers) retained versus the baseline, citation rate, and reading sample answers (Q8, Q19, Q21, Q22). It is not a formal accuracy evaluation; with 22 queries and one reference answer it is indicative only. SharePoint quality calls are lower confidence because two runs of the same SharePoint agent differ noticeably.</li>
<li><b>Baselines:</b> the SharePoint original was changed by you at 22:09 IST (model knowledge off and "avoid multiple knowledgebase calls"). Baseline 1 was captured before that; baselines 2a and 2b were captured after.</li>
<li><b>Foundry agent:</b> <code>orange-agent-xd5kzq7smb</code>, version 3 and version 4 (v4 adds tool_choice required; each was latest at its test time), model gpt-5.6-luna, reasoning effort low, MCP tool pointing at knowledge base <code>knowledgebase624</code> on <code>pwaisearch-eus</code>. Streaming Responses API from this machine, so network time is included; Copilot Studio numbers are also measured from this machine.</li>
<li><b>10-source runs:</b> both agents were benchmarked at the same time from the same machine (independent backends), 5 runs of each of the 22 queries. Compared with the earlier 1-source setups, latency rose: SharePoint 17s to about 20.7s, AI Search 8.5s to about 10.0s. SharePoint retrieval is about 14.7s p50; AI Search retrieval about 3.1s p50, so the gap between them widened.</li>\n<li><b>Excluded:</b> earlier clone runs with broken retrieval (null search results and SystemError answers) are excluded from this report.</li>
</ul>
</body></html>"""
(H / "final_report.html").write_text(doc)
print("wrote", H / "final_report.html", len(doc))
