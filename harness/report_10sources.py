"""Build harness/report_10sources.html: summary comparison + all 110 conversations per agent."""
import html
import json
import re
import statistics as st
from pathlib import Path

from analyze import phases, pct

H = Path(__file__).parent
rows = [json.loads(l) for l in open(H / "results.jsonl")]
AGENTS = [("PWSharepointAgent", "sp10-baseline", "Copilot Studio + SharePoint (10 sources)"),
          ("PWAISearchAgent", "ai10-baseline", "Copilot Studio + Azure AI Search (10 indexes)")]
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
Run on 2026-10-05, both benchmarks in parallel from the same machine. Click an answer to expand it. Yellow rows are over 30s, red rows are empty or fallback answers.</div>

<h2>Summary: SharePoint vs Azure AI Search</h2>
<table class="summary"><thead><tr><th>Metric</th><th>PWSharepointAgent<br><small>SharePoint, 10 folders</small></th>
<th>PWAISearchAgent<br><small>AI Search, 10 indexes</small></th><th>SharePoint / AI Search</th></tr></thead><tbody>{summary_rows}</tbody></table>
<ul>
<li>AI Search is about {sp['p50'] / ai['p50']:.1f}x faster at p50. The whole gap is the knowledge-search phase: SharePoint search is {sp['search'] - ai['search']:.1f}s slower at p50, versus a {sp['p50'] - ai['p50']:.1f}s total gap (AI Search generation is about 1s slower, which offsets some of it).</li>
<li>AI Search is much more predictable: within-question std dev {ai['qsd']:.1f}s versus {sp['qsd']:.1f}s for SharePoint, and its slowest conversation took {ai['mx']:.0f}s versus {sp['mx']:.0f}s.</li>
<li>Configuration differences: PWAISearchAgent had "Allow ungrounded responses" (model knowledge) ON during this run and was turned off afterwards, so these numbers do not yet reflect that change. PWSharepointAgent has model knowledge off.</li>
<li>Phase definitions: plan is the time to the first plan event; search runs from the first search step to the first answer token; generation is the remainder. Search steps counted from DynamicPlanStepFinished events.</li>
<li>Both agents answered every conversation (no empty, fallback or error answers in this run). In the earlier functional test SharePoint did return one empty answer and one fallback on a batch_10 question, so it is not fully reliable.</li>
</ul>

{detail("PWSharepointAgent", "PWSharepointAgent (Copilot Studio + SharePoint)")}
{detail("PWAISearchAgent", "PWAISearchAgent (Copilot Studio + Azure AI Search)")}
</body></html>"""
(H / "report_10sources.html").write_text(doc)
print("wrote", len(doc))
