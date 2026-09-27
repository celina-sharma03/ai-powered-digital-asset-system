"""Search quality evaluation.

How relevance is judged
  Every downloaded file was found through a known source query ("woman with cat",
  "construction site", ...), recorded in data/manifest.csv. The indexer never reads
  the manifest. Here it serves as a *silver-standard* label: a result counts as
  relevant if its source query is one of the query's expected source queries.
  These labels are noisy (a "cat" photo can also show a woman), so the report lists
  the top results with their source titles for a manual check, and
  evaluation/judgments_override.json can overrule individual labels.

Metrics per query (computed for every retrieval mode, to show what each signal adds)
  P@5, P@10  share of the top 5 / 10 results that are relevant
  R@20       share of all relevant assets found in the top 20
  MRR        1 / rank of the first relevant result

Requires the API to be running (it uses the same /api/search endpoint as the UI).
Usage (from repo root):  backend/.venv/Scripts/python backend/scripts/evaluate.py
"""

import csv
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.config import settings  # noqa: E402

EVAL_DIR = REPO_ROOT / "evaluation"
API = "http://127.0.0.1:8000"
MODES = ["hybrid", "hybrid_plain", "visual", "text", "keyword"]
TOP_K = 20


def load_manifest() -> dict[str, dict]:
    """filename -> manifest row. Duplicates planted as copy_of_<name> map to the original."""
    path = settings.media_dir.parent / "manifest.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        return {Path(r["relative_path"]).name: r for r in csv.DictReader(fh)}


def label_for(filename: str, manifest: dict) -> dict | None:
    return manifest.get(filename.removeprefix("copy_of_"))


def is_relevant(q: dict, filename: str, manifest: dict, overrides: dict) -> bool:
    if filename in overrides.get(q["id"], {}):
        return bool(overrides[q["id"]][filename])
    row = label_for(filename, manifest)
    return bool(row) and row["source_query"] in q["expected"]["source_queries"] \
        and row["media_type"] in q["expected"]["media_types"]


def total_relevant(q: dict, manifest: dict) -> int:
    return sum(1 for r in manifest.values()
               if r["source_query"] in q["expected"]["source_queries"] and r["media_type"] in q["expected"]["media_types"])


def metrics(flags: list[bool], n_relevant: int) -> dict:
    first = next((i + 1 for i, f in enumerate(flags) if f), None)
    return {
        "P@5": sum(flags[:5]) / 5,
        "P@10": sum(flags[:10]) / 10,
        "R@20": (sum(flags[:20]) / n_relevant) if n_relevant else 0.0,
        "MRR": 1 / first if first else 0.0,
    }


def main() -> None:
    queries = json.loads((EVAL_DIR / "queries.json").read_text(encoding="utf-8"))
    override_file = EVAL_DIR / "judgments_override.json"
    overrides = json.loads(override_file.read_text(encoding="utf-8")) if override_file.exists() else {}
    manifest = load_manifest()

    report = {"per_query": [], "summary": {}}
    with httpx.Client(base_url=API, timeout=120) as client:
        for q in queries:
            n_rel = total_relevant(q, manifest)
            entry = {**q, "n_relevant_in_dataset": n_rel, "modes": {}}
            for mode in MODES:
                res = client.get("/api/search", params={"q": q["query"], "mode": mode, "limit": TOP_K}).json()
                flags = [is_relevant(q, r["filename"], manifest, overrides) for r in res["results"]]
                entry["modes"][mode] = {"metrics": metrics(flags, n_rel), "top": [
                    {"filename": r["filename"], "type": r["file_type"], "relevant": f,
                     "source_query": (label_for(r["filename"], manifest) or {}).get("source_query", "?"),
                     "title": ((label_for(r["filename"], manifest) or {}).get("title") or "")[:80],
                     "signals": list(r["matches"].keys())}
                    for r, f in zip(res["results"][:10], flags)]}
            report["per_query"].append(entry)
            m = entry["modes"]["hybrid"]["metrics"]
            print(f"{q['id']} {q['query'][:45]:45}  P@5={m['P@5']:.2f}  P@10={m['P@10']:.2f}  "
                  f"R@20={m['R@20']:.2f}  MRR={m['MRR']:.2f}")

    for mode in MODES:
        rows = [e["modes"][mode]["metrics"] for e in report["per_query"]]
        report["summary"][mode] = {k: round(sum(r[k] for r in rows) / len(rows), 3) for k in rows[0]}

    (EVAL_DIR / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (EVAL_DIR / "results.md").write_text(render_markdown(report), encoding="utf-8")
    print("\nMean over queries:")
    for mode, m in report["summary"].items():
        print(f"  {mode:8} " + "  ".join(f"{k}={v:.3f}" for k, v in m.items()))
    print(f"\nWrote {EVAL_DIR / 'results.md'}")


def render_markdown(report: dict) -> str:
    out = ["# Search evaluation results (auto-generated)", "",
           "Generated by `backend/scripts/evaluate.py`. See `evaluation/README.md` for the method.", "",
           "## Summary: mean over all queries", "",
           "| Mode | P@5 | P@10 | R@20 | MRR |", "|---|---|---|---|---|"]
    for mode, m in report["summary"].items():
        out.append(f"| {mode} | {m['P@5']:.2f} | {m['P@10']:.2f} | {m['R@20']:.2f} | {m['MRR']:.2f} |")
    out += ["", "## Per query (hybrid mode)", "",
            "| ID | Query | Relevant in dataset | P@5 | P@10 | R@20 | MRR |", "|---|---|---|---|---|---|---|"]
    for e in report["per_query"]:
        m = e["modes"]["hybrid"]["metrics"]
        out.append(f"| {e['id']} | {e['query']} | {e['n_relevant_in_dataset']} | {m['P@5']:.2f} | "
                   f"{m['P@10']:.2f} | {m['R@20']:.2f} | {m['MRR']:.2f} |")
    out += ["", "## Top-10 results per query (hybrid)", ""]
    for e in report["per_query"]:
        out += [f"### {e['id']}: “{e['query']}”", f"*Goal:* {e['user_goal']}  ", ""]
        out += ["| # | File | Type | Relevant | Source query (label) | Source title | Signals |",
                "|---|---|---|---|---|---|---|"]
        for i, t in enumerate(e["modes"]["hybrid"]["top"], 1):
            out.append(f"| {i} | {t['filename']} | {t['type']} | {'✅' if t['relevant'] else '❌'} | "
                       f"{t['source_query']} | {t['title'].replace('|', '/')} | {', '.join(t['signals'])} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    main()
