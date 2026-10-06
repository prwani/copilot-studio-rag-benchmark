"""Build harness/report_10sources.html: summary comparison + all 110 conversations per agent."""
import html
import os
import json
import re
import statistics as st
from pathlib import Path

from analyze import phases, pct

H = Path(__file__).parent
SDK = os.environ.get("SDK") == "1"
rows = [json.loads(l) for f in (["sdk_original.jsonl", "sdk_aisearch.jsonl"] if SDK else ["results.jsonl"]) for l in open(H / f)]
AGENTS = [("PWSharepointAgent", "sdk10-original" if SDK else "sp10-baseline", "Copilot Studio + SharePoint (10 sources)"),
          ("PWAISearchAgent", "sdk10-aisearch" if SDK else "ai10-baseline", "Copilot Studio + Azure AI Search (10 indexes)")]
BAD = re.compile(r"not sure how to help|something unexpected|SystemError|Error code", re.I)
CITE = re.compile(r"https?://|\[\d+\]")


def ans(r):
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message":
            return x.get("text", "")
    return ""


def fix(t):
    try:
        return t.encode("latin-1").decode("utf-8")
    except Exception:
        return t


def steps(r):
    return sum(1 for a in r["activities"] if (a.get("raw") or {}).get("name") == "DynamicPlanStepFinished")


data = {}
for name, label, _ in AGENTS:
    recs = sorted((r for r in rows if r["label"] == label), key=lambda r: (r["run_index"], r["question_index"]))
    out = []
    for r in recs:
        p = phases(r)
        a = ans(r)
        out.append(dict(run=r["run_index"], q=r["question_index"], question=r["question"], total=r["total_time_ms"] / 1000,
                        plan=p["plan_s"] / 1000 if p["plan_s"] else None, search=p["search_s"] / 1000 if p["search_s"] else None,
                        gen=p["gen_s"] / 1000 if p["gen_s"] else None, steps=steps(r), length=len(a), cited=bool(CITE.search(a)),
                        bad=bool(BAD.search(a)) or not a.strip(), started=r["started_at"][11:19], answer=fix(a)))
    data[name] = out


def col(v, k):
    return [x[k] for x in v if x[k] is not None]


def f(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def summ(v):
    t = col(v, "total")
    byrun = {}
    for x in v:
        byrun.setdefault(x["run"], []).append(x["total"])
    perq = {}
    for x in v:
        perq.setdefault(x["q"], []).append(x["total"])
    return dict(n=len(v), mean=st.mean(t), p50=pct(t, .5), p90=pct(t, .9), p95=pct(t, .95), mn=min(t), mx=max(t), sd=st.pstdev(t),
                plan=pct(col(v, "plan"), .5), search=pct(col(v, "search"), .5), search95=pct(col(v, "search"), .95),
                gen=pct(col(v, "gen"), .5), steps=st.mean(x["steps"] for x in v),
                zero=sum(x["steps"] == 0 for x in v), length=st.mean(x["length"] for x in v),
                cited=sum(x["cited"] for x in v) / len(v), bad=sum(x["bad"] for x in v),
                runs=[pct(b, .5) for _, b in sorted(byrun.items())], qsd=st.mean(st.pstdev(b) for b in perq.values()),
                over30=sum(x > 30 for x in t))


S = {n: summ(data[n]) for n, _, _ in AGENTS}
sp, ai = S["PWSharepointAgent"], S["PWAISearchAgent"]


def ratio(a, b):
    return f"{a / b:.1f}x" if b else "-"


metrics = [
    ("Conversations (22 queries x 5 runs)", lambda s: f"{s['n']}", None),
    ("Total latency p50 (s)", lambda s: f(s["p50"]), "p50"),
    ("Total latency mean (s)", lambda s: f(s["mean"]), "mean"),
    ("Total latency p90 (s)", lambda s: f(s["p90"]), "p90"),
    ("Total latency p95 (s)", lambda s: f(s["p95"]), "p95"),
    ("Fastest / slowest (s)", lambda s: f"{s['mn']:.1f} / {s['mx']:.1f}", None),
    ("Std dev of total latency (s)", lambda s: f(s["sd"]), "sd"),
    ("Avg within-question std dev (s)", lambda s: f(s["qsd"]), "qsd"),
    ("Conversations over 30s", lambda s: f"{s['over30']}", None),
    ("Per-run p50 (s), runs 1-5", lambda s: ", ".join(f"{x:.1f}" for x in s["runs"]), None),
    ("Plan phase p50 (s)", lambda s: f(s["plan"]), "plan"),
    ("Knowledge search phase p50 (s)", lambda s: f(s["search"]), "search"),
    ("Knowledge search phase p95 (s)", lambda s: f(s["search95"]), "search95"),
    ("Answer generation phase p50 (s)", lambda s: f(s["gen"]), "gen"),
    ("Search steps per question (avg)", lambda s: f(s["steps"], 2), None),
    ("Conversations with no search step", lambda s: f"{s['zero']}", None),
    ("Avg answer length (chars)", lambda s: f"{s['length']:.0f}", None),
    ("Answers with a citation", lambda s: f"{s['cited']:.0%}", None),
    ("Empty / fallback / error answers", lambda s: f"{s['bad']}", None),
]
summary_rows = ""
for label, fn, key in metrics:
    rt = ratio(sp[key], ai[key]) if key else ""
    summary_rows += f"<tr><td>{label}</td><td class='num'>{fn(sp)}</td><td class='num'>{fn(ai)}</td><td class='num'>{rt}</td></tr>"

CAUSE = (sp["search"] - ai["search"]) / (sp["p50"] - ai["p50"])


def detail(name, title):
    out = []
    for x in data[name]:
        cls = " class='slow'" if x["total"] > 30 else (" class='bad'" if x["bad"] else "")
        out.append(f"<tr{cls}><td class='num'>{x['run']}</td><td class='num'>{x['q']}</td><td class='q'>{html.escape(x['question'])}</td>"
                   f"<td>{x['started']}</td><td class='num b'>{x['total']:.1f}</td><td class='num'>{f(x['plan'])}</td>"
                   f"<td class='num'>{f(x['search'])}</td><td class='num'>{f(x['gen'])}</td><td class='num'>{x['steps']}</td>"
                   f"<td class='num'>{x['length']}</td><td>{'yes' if x['cited'] else 'no'}</td>"
                   f"<td class='ans'><details><summary>{html.escape(x['answer'][:70])}</summary>{html.escape(x['answer'])}</details></td></tr>")
    return (f"<h2>{html.escape(title)}: all {len(data[name])} conversations</h2>"
            "<table class='detail'><thead><tr><th>Run</th><th>Q#</th><th>Question</th><th>Start (UTC)</th><th>Total (s)</th>"
            "<th>Plan (s)</th><th>Search (s)</th><th>Generation (s)</th><th>Search steps</th><th>Answer chars</th><th>Cited</th>"
            f"<th>Answer</th></tr></thead><tbody>{''.join(out)}</tbody></table>")


doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>10-source RAG agent benchmark</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;max-width:1600px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;margin:10px 0 28px}}
th,td{{border:1px solid #ddd;padding:5px 8px;vertical-align:top}} th{{background:#f0f3f7;text-align:left;position:sticky;top:0}}
td.num{{text-align:right;white-space:nowrap}} td.b{{font-weight:600}} td.q{{max-width:360px}} td.ans{{max-width:520px}}
table.summary{{width:auto;min-width:760px;font-size:14px}} table.summary td:first-child{{font-weight:500}}
tr.slow td{{background:#fff3cd}} tr.bad td{{background:#fde8e8}}
details summary{{cursor:pointer;color:#245}} details{{white-space:pre-wrap}}
.sub{{color:#666;margin-bottom:16px}}
</style></head><body>
<h1>10-source RAG agent benchmark (Copilot Studio)</h1>
<div class="sub">Both agents now have 10 knowledge sources (about 3 GB of data). 22 user queries x 5 runs each, one new conversation per query.
{"Driver: Copilot Studio Agents SDK (CopilotClient). " if SDK else ""}Run on {"2026-10-06" if SDK else "2026-10-05"}, both benchmarks in parallel from the same machine. Click an answer to expand it. Yellow rows are over 30s, red rows are empty or fallback answers.</div>

<h2>Summary: SharePoint vs Azure AI Search</h2>
<table class="summary"><thead><tr><th>Metric</th><th>PWSharepointAgent<br><small>SharePoint, 10 folders</small></th>
<th>PWAISearchAgent<br><small>AI Search, 10 indexes</small></th><th>SharePoint / AI Search</th></tr></thead><tbody>{summary_rows}</tbody></table>
<ul>
<li>AI Search is about {sp['p50'] / ai['p50']:.1f}x faster at p50. The whole gap is the knowledge-search phase: SharePoint search is {sp['search'] - ai['search']:.1f}s slower at p50, versus a {sp['p50'] - ai['p50']:.1f}s total gap (AI Search generation is about 1s slower, which offsets some of it).</li>
<li>AI Search is much more predictable: within-question std dev {ai['qsd']:.1f}s versus {sp['qsd']:.1f}s for SharePoint, and its slowest conversation took {ai['mx']:.0f}s versus {sp['mx']:.0f}s.</li>
<li>Configuration: {"PWAISearchAgent had 'Allow ungrounded responses' turned OFF and published (2026-10-05 17:05 UTC) before this run, unlike the 2026-10-05 benchmark. PWSharepointAgent has model knowledge off." if SDK else "PWAISearchAgent had 'Allow ungrounded responses' (model knowledge) ON during this run and was turned off afterwards. PWSharepointAgent has model knowledge off."}</li>
<li>Phase definitions: plan is the time to the first plan event; search runs from the first search step to the first answer token; generation is the remainder. Search steps counted from DynamicPlanStepFinished events.</li>
<li>Answer health: {"; ".join(f"{n}: {sum(x['bad'] for x in data[n])} empty/fallback answers out of {len(data[n])}" for n in data)}.</li>
</ul>


<h2>Benchmark notes, setup and references</h2>
<ul>
<li><b>1. Benchmark design.</b> Two Copilot Studio agents are compared: <b>PWSharepointAgent</b> (10 SharePoint knowledge sources, searched by Copilot Studio's built-in SharePoint knowledge) and <b>PWAISearchAgent</b> (10 Azure AI Search knowledge sources, one index per batch). 22 queries x 5 runs per agent, one new conversation per query, latency measured end to end.
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-copilot-studio">Knowledge sources overview</a> |
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-sharepoint">SharePoint as knowledge</a> |
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-azure-ai-search">Azure AI Search as knowledge</a></li>
<li><b>2. Benchmark scripts.</b> Python, using the Microsoft 365 Agents SDK Copilot Studio client (<code>microsoft-agents-copilotstudio-client</code>: <code>CopilotClient</code>, <code>start_conversation</code>, <code>ask_question</code>). Every streamed activity is timestamped on arrival, so plan, search and generation phases are derived from the plan events. The earlier benchmark used the REST endpoint behind the same client.
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/publication-integrate-web-or-native-app-m365-agents-sdk">Integrate with the Microsoft 365 Agents SDK</a> |
 <a href="https://github.com/microsoft/Agents-for-python/tree/main/libraries/microsoft-agents-copilotstudio-client">Python client library</a> |
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/kit-agent-debugger">Agent debugger (performance timeline)</a> |
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/kit-enable-application-insights">Application Insights</a></li>
<li><b>3. Dataset.</b> About 1,000 PDF files (10 batches of about 100), each a research paper, about 3 GB in total. The same files back both agents: 10 SharePoint folders, and 10 Azure AI Search indexes (about 42,900 chunks, 1.9 GB of index).
 <a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-unstructured-data">Unstructured data as knowledge</a></li>
<li><b>4. SharePoint indexer permissions.</b> The Azure AI Search SharePoint Online indexer (preview) reads with Microsoft Graph application permissions <b>Files.Read.All</b> and <b>Sites.Read.All</b> (admin consent required), or <b>Sites.Selected</b> plus Files.Read.All to limit it to chosen sites. Delegated equivalents exist for the delegated-auth option.
 <a href="https://learn.microsoft.com/en-us/azure/search/search-howto-index-sharepoint-online">SharePoint Online indexer</a></li>
<li><b>5. Adding a SharePoint indexer to Azure AI Search.</b> Register an Entra app and grant the permissions above, create a SharePoint data source, create the index and an indexer, then run it and monitor status. Follow the doc rather than a copied step list, since the feature is in preview and changes.
 <a href="https://learn.microsoft.com/en-us/azure/search/search-howto-index-sharepoint-online">Index data from SharePoint Online</a></li>
<li><b>6. Results (total latency).</b> PWSharepointAgent p50 {sp['p50']:.1f}s, p90 {sp['p90']:.1f}s, p95 {sp['p95']:.1f}s. PWAISearchAgent p50 {ai['p50']:.1f}s, p90 {ai['p90']:.1f}s, p95 {ai['p95']:.1f}s.</li>
<li><b>7. Private endpoint for Azure AI Search.</b> Copilot Studio reaches a private AI Search through the AI Search connector running in a Power Platform environment that is VNet-integrated (delegated subnet), with the search service behind a private endpoint in the same VNet. Reference architecture and security notes:
 <a href="https://github.com/Azure-Samples/Copilot-Studio-with-Azure-AI-Search/blob/main/docs/security_considerations.md#architecture-diagram">Copilot Studio with Azure AI Search: security considerations</a> |
 <a href="https://learn.microsoft.com/en-us/power-platform/admin/vnet-support-overview">Power Platform virtual network support</a> |
 <a href="https://learn.microsoft.com/en-us/azure/search/service-create-private-endpoint">Private endpoint for Azure AI Search</a></li>
<li><b>8. Phase definitions.</b> Plan: time to the first plan event. Search: first search step to the first answer text. Generation: the remainder. Percentiles use nearest-rank on 110 samples per agent.</li>
<li><b>9. Caveats.</b> Both benchmarks ran in parallel from one machine. Answer quality was spot-checked, not scored. Results depend on region, tenant load and agent configuration at the time of the run.</li>
</ul>

{detail("PWSharepointAgent", "PWSharepointAgent (Copilot Studio + SharePoint)")}
{detail("PWAISearchAgent", "PWAISearchAgent (Copilot Studio + Azure AI Search)")}
</body></html>"""
(H / ("report_10sources_sdk.html" if SDK else "report_10sources.html")).write_text(doc)
print("wrote", len(doc))
