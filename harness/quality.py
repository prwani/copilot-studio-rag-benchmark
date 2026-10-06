"""Answer-quality comparison vs a reference label. Usage: python quality.py"""
import json, re, statistics as st
rows = [json.loads(l) for l in open("results.jsonl")]
def ans(r):
    for a in r["activities"]:
        x = a.get("raw") or {}
        if a["phase"] == "turn" and x.get("type") == "message":
            return x.get("text", "")
    return ""
by = {}
for r in rows:
    if r["status"] == "ok" and not r["label"].startswith(("INVALID", "guard", "sp-guard", "smoke")):
        by.setdefault((r["label"], r["agent"]), {})[r["question_index"]] = ans(r)
clean = lambda t: re.sub(r"\[\d+\]|https?://\S+|\d{4}\.\d{4,5}v?\d*", " ", t)
words = lambda t: {w for w in re.findall(r"[a-z]{5,}", clean(t).lower())}
nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", clean(t)))
IDK = re.compile(r"(couldn.t find|could not find|no (relevant )?information|don.t have|unable to find|not (found|available)|sorry)", re.I)
def score(a, ref):
    rw, rn = words(ref), nums(ref)
    wc = len(rw & words(a)) / len(rw) if rw else 1
    nc = len(rn & nums(a)) / len(rn) if rn else 1
    return wc, nc
def evaluate(label, agent, refs):
    d = by[(label, agent)]
    wcs, ncs, cites, idks, lens = [], [], 0, 0, []
    for q, a in d.items():
        r = refs[q]
        wc, nc = score(a, r); wcs.append(wc); ncs.append(nc)
        cites += "sharepoint.com" in a or "http" in a or bool(re.search(r"\[\d+\]", a))
        idks += bool(IDK.search(a)); lens.append(len(a))
    n = len(d)
    return dict(word=st.mean(wcs), num=st.mean(ncs), cite=cites / n, idk=idks, len=st.mean(lens))
if __name__ == "__main__":
    A = "aisearch-opt"
    ref = {q: by[("ai-baseline_r1", A)][q] for q in by[("ai-baseline_r1", A)]}
    print(f"{'label':32} word-cov num-cov cited idk avgLen   (ref = ai-baseline_r1)")
    for (l, a) in sorted(by):
        if a == A:
            m = evaluate(l, a, ref)
            print(f"{l:32} {m['word']:.2f}     {m['num']:.2f}    {m['cite']:.0%}  {m['idk']}   {m['len']:.0f}")
