"""Table 2 scoring: CLAIR-Fin-specific metrics (coverage, faithfulness, custody, AEA impact, etc.)."""
from __future__ import annotations
import argparse
import json
import logging
import statistics
from pathlib import Path
from clairfin.utils.logging_setup import configure_logging
from evaluation.correctness_judge import grade_correctness
from evaluation.gold_data import list_available_chapters

logger = logging.getLogger(__name__)

EVALUATED_OUTPUT_DIR = Path(__file__).parent / "evaluated_output"
RESULTS_DIR = Path(__file__).parent / "results"


def load_chapter_responses(chapter: str) -> list[dict]:
    slug = chapter if str(chapter).startswith("chapter_") else f"chapter_{chapter}"
    path = EVALUATED_OUTPUT_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} doesn't exist — run `python -m evaluation.generate_responses --chapter {chapter}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def score_question(row: dict) -> dict:
    claims = row.get("claims") or []
    non_abstained_claims = [c for c in claims if c.get("audit_passed")]

    if row["abstained"]:
        correctness = "abstained"
    else:
        verdict = grade_correctness(row["question"], row["gold_answer"], row["final_answer"])
        correctness = verdict.label

    citation_recall_ratios = []
    for c in non_abstained_claims:
        considered = len(c.get("sources_considered") or [])
        cited = len(c.get("cited_source_ids") or [])
        if considered:
            citation_recall_ratios.append(cited / considered)

    hri_values = [c["hallucination_risk_index"] for c in non_abstained_claims if c.get("hallucination_risk_index") is not None]

    debated_claims = [c for c in claims if c.get("was_debated")]
    fast_path_claims = [c for c in claims if not c.get("was_debated")]

    return {
        "id": row["id"],
        "question": row["question"],
        "gold_answer": row["gold_answer"],
        "final_answer": row["final_answer"],
        "abstained": row["abstained"],
        "correctness": correctness,
        "n_claims": len(claims),
        "n_audit_passed": len(non_abstained_claims),
        "citation_recall_ratios": citation_recall_ratios,
        "max_hri": max(hri_values) if hri_values else None,
        "n_debated": len(debated_claims),
        "n_debated_audit_passed": sum(1 for c in debated_claims if c.get("audit_passed")),
        "n_fast_path": len(fast_path_claims),
        "n_fast_path_audit_passed": sum(1 for c in fast_path_claims if c.get("audit_passed")),
        "authority_docket_summary": row.get("authority_docket_summary"),
    }


def _quartile_bins(values: list[float]) -> list[tuple[str, float, float]]:
    if not values:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cuts = [sorted_vals[int(n * frac)] if int(n * frac) < n else sorted_vals[-1] for frac in (0.25, 0.5, 0.75)]
    return [
        ("Q1 (lowest risk)", 0.0, cuts[0]),
        ("Q2", cuts[0], cuts[1]),
        ("Q3", cuts[1], cuts[2]),
        ("Q4 (highest risk)", cuts[2], 1.0),
    ]


def aggregate_results(chapter_label: str, rows: list[dict]) -> dict:
    n = len(rows)
    non_abstained = [r for r in rows if not r["abstained"]]
    coverage = len(non_abstained) / n if n else 0.0
    selective_accuracy = (
        sum(1 for r in non_abstained if r["correctness"] == "correct") / len(non_abstained) if non_abstained else 0.0
    )

    total_audit_passed = sum(r["n_audit_passed"] for r in rows)
    total_claims_non_abstained_q = sum(r["n_claims"] for r in non_abstained)
    faithfulness_rate = total_audit_passed / total_claims_non_abstained_q if total_claims_non_abstained_q else 0.0

    all_recall_ratios = [ratio for r in rows for ratio in r["citation_recall_ratios"]]
    citation_recall_proxy = statistics.mean(all_recall_ratios) if all_recall_ratios else 0.0
    citation_precision_proxy = faithfulness_rate

    hri_rows = [r for r in rows if r["max_hri"] is not None]
    hri_values = [r["max_hri"] for r in hri_rows]
    wrong_flags = [1 if r["correctness"] == "incorrect" else 0 for r in hri_rows]
    if len(hri_values) >= 2 and len(set(hri_values)) > 1:
        correlation = statistics.correlation(hri_values, wrong_flags)
    else:
        correlation = None

    bins = _quartile_bins(hri_values)
    binned_table = []
    for label, lo, hi in bins:
        in_bin = [(v, w) for v, w in zip(hri_values, wrong_flags) if lo <= v <= hi]
        wrong_rate = sum(w for _, w in in_bin) / len(in_bin) if in_bin else None
        binned_table.append({"bin": label, "range": f"[{lo:.2f}, {hi:.2f}]", "n": len(in_bin), "observed_wrong_rate": wrong_rate})

    total_debated = sum(r["n_debated"] for r in rows)
    total_fast_path = sum(r["n_fast_path"] for r in rows)
    total_claims_all = total_debated + total_fast_path
    debate_utilization_rate = total_debated / total_claims_all if total_claims_all else 0.0
    debated_audit_pass_rate = (
        sum(r["n_debated_audit_passed"] for r in rows) / total_debated if total_debated else None
    )
    fast_path_audit_pass_rate = (
        sum(r["n_fast_path_audit_passed"] for r in rows) / total_fast_path if total_fast_path else None
    )

    docket_summaries = [r["authority_docket_summary"] for r in rows if r.get("authority_docket_summary")]
    total_decisions = sum(d.get("decisions", 0) for d in docket_summaries)
    total_changed = sum(d.get("changed_outcome", 0) for d in docket_summaries)
    aea_impact_rate = total_changed / total_decisions if total_decisions else None
    modalities_favored: dict[str, int] = {}
    for d in docket_summaries:
        for modality, count in (d.get("modalities_favored_by_asymmetry") or {}).items():
            modalities_favored[modality] = modalities_favored.get(modality, 0) + count

    return {
        "chapter": chapter_label,
        "n_questions": n,
        "coverage": coverage,
        "selective_accuracy": selective_accuracy,
        "faithfulness_rate": faithfulness_rate,
        "citation_precision_proxy": citation_precision_proxy,
        "citation_recall_proxy": citation_recall_proxy,
        "hri_wrong_correlation": correlation,
        "hri_binned_table": binned_table,
        "correctness_counts": {
            label: sum(1 for r in rows if r["correctness"] == label)
            for label in ("correct", "partial", "incorrect", "abstained")
        },
        "debate_utilization_rate": debate_utilization_rate,
        "debated_audit_pass_rate": debated_audit_pass_rate,
        "fast_path_audit_pass_rate": fast_path_audit_pass_rate,
        "total_claims": total_claims_all,
        "aea_impact_rate": aea_impact_rate,
        "aea_decisions": total_decisions,
        "aea_changed_outcome": total_changed,
        "modalities_favored_by_asymmetry": modalities_favored,
    }


def render_markdown(aggregate: dict) -> str:
    def fmt(x) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    corr = aggregate["correctness_counts"]
    table = f"""### {aggregate['chapter']} — CLAIR-Fin-Specific Metrics

| # | Metric | What It Measures | Data Source | Score |
|---|--------|-------------------|--------------|-------|
| 1 | Graded Answer Correctness | 4-way grade per claim: Correct / Partially Correct / Incorrect / Abstained, vs. `questions.md` gold answers | `final_answer`, per-claim `answer_fragment` in `answer.json` | Correct: {corr['correct']}, Partial: {corr['partial']}, Incorrect: {corr['incorrect']}, Abstained: {corr['abstained']} |
| 2 | Faithfulness Rate | % of published claims where the citation-entailment gate passed | `audit_passed` per claim | {fmt(aggregate['faithfulness_rate'])} |
| 3 | Citation Precision / Recall | Precision/recall of cited evidence against what actually supports the claim (proxies — see script docstring) | `cited_source_ids` vs. `sources_considered` | {fmt(aggregate['citation_precision_proxy'])} / {fmt(aggregate['citation_recall_proxy'])} |
| 4 | Selective Accuracy / Coverage | Accuracy on non-abstained claims vs. % of questions answered at all | `abstained` flag, `entailment_pass_threshold` | {fmt(aggregate['selective_accuracy'])} / {fmt(aggregate['coverage'])} |
| 5 | HRI Calibration (ECE) | Whether higher Hallucination Risk Index claims are empirically wrong more often | `hallucination_risk_index` + correctness grade (Metric 1) | correlation = {fmt(aggregate['hri_wrong_correlation'])} |
| 6 | Debate Utilization & Efficacy | % of claims escalated to multi-agent debate, and audit-pass-rate for debated vs. fast-pathed claims | `was_debated`, `audit_passed` per claim | utilization = {fmt(aggregate['debate_utilization_rate'])}; debated pass rate = {fmt(aggregate['debated_audit_pass_rate'])}; fast-path pass rate = {fmt(aggregate['fast_path_audit_pass_rate'])} |
| 7 | Authority Docket / AEA Impact Rate | % of AEA scoring decisions where asymmetric weighting changed the winning modality vs. equal-weight voting | `authority_docket_summary` per question | {fmt(aggregate['aea_impact_rate'])} ({aggregate['aea_changed_outcome']}/{aggregate['aea_decisions']} decisions) |

_n = {aggregate['n_questions']} questions, {aggregate['total_claims']} claims._

#### HRI risk-bin table

| Bin | HRI Range | n | Observed Wrong Rate |
|-----|-----------|---|----------------------|
"""
    for row in aggregate["hri_binned_table"]:
        table += f"| {row['bin']} | {row['range']} | {row['n']} | {fmt(row['observed_wrong_rate'])} |\n"

    favored = aggregate["modalities_favored_by_asymmetry"]
    if favored:
        table += "\n#### Modalities favored by AEA asymmetry\n\n"
        table += "| Modality | Times Favored |\n|---|---|\n"
        for modality, count in sorted(favored.items(), key=lambda kv: -kv[1]):
            table += f"| {modality} | {count} |\n"

    return table


def render_combined_markdown(all_aggregates: list[dict]) -> str:
    def fmt(x) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    header = "| Metric | " + " | ".join(a["chapter"] for a in all_aggregates) + " |"
    sep = "|---|" + "---|" * len(all_aggregates)
    metric_rows = [
        ("Faithfulness Rate", lambda a: fmt(a["faithfulness_rate"])),
        ("Citation Precision (proxy)", lambda a: fmt(a["citation_precision_proxy"])),
        ("Citation Recall (proxy)", lambda a: fmt(a["citation_recall_proxy"])),
        ("Selective Accuracy", lambda a: fmt(a["selective_accuracy"])),
        ("Coverage", lambda a: fmt(a["coverage"])),
        ("HRI-Wrong Correlation", lambda a: fmt(a["hri_wrong_correlation"])),
        ("Debate Utilization Rate", lambda a: fmt(a["debate_utilization_rate"])),
        ("Debated Claims Audit-Pass Rate", lambda a: fmt(a["debated_audit_pass_rate"])),
        ("Fast-Path Claims Audit-Pass Rate", lambda a: fmt(a["fast_path_audit_pass_rate"])),
        ("AEA Impact Rate", lambda a: fmt(a["aea_impact_rate"])),
        ("Correct", lambda a: str(a["correctness_counts"]["correct"])),
        ("Partial", lambda a: str(a["correctness_counts"]["partial"])),
        ("Incorrect", lambda a: str(a["correctness_counts"]["incorrect"])),
        ("Abstained", lambda a: str(a["correctness_counts"]["abstained"])),
    ]
    rows = [f"| {label} | " + " | ".join(fn(a) for a in all_aggregates) + " |" for label, fn in metric_rows]
    return "## CLAIR-Fin-Specific Metrics — All Chapters\n\n" + "\n".join([header, sep, *rows]) + "\n"


def main() -> None:
    configure_logging("eval_clairfin_metrics")
    parser = argparse.ArgumentParser(description="Score CLAIR-Fin-Specific Metrics from evaluation/evaluated_output/")
    parser.add_argument("--chapter", type=int, default=None, help="Chapter number (1, 2, 3, ...)")
    parser.add_argument("--all", action="store_true", help="Score every chapter with a evaluated_output/chapter_N.json file")
    args = parser.parse_args()

    if not args.all and args.chapter is None:
        parser.error("pass --chapter N or --all")

    chapters = list_available_chapters() if args.all else [str(args.chapter)]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_aggregates = []
    for chapter in chapters:
        raw_rows = load_chapter_responses(chapter)
        chapter_label = raw_rows[0]["chapter"] if raw_rows else chapter
        logger.info("Scoring CLAIR-Fin metrics for %s (%d questions)", chapter_label, len(raw_rows))

        scored_rows = []
        for row in raw_rows:
            try:
                scored_rows.append(score_question(row))
            except Exception:
                logger.exception("Question %s failed, skipping", row.get("id"))

        aggregate = aggregate_results(chapter_label, scored_rows)
        all_aggregates.append(aggregate)
        slug = f"chapter_{chapter}" if not str(chapter).startswith("chapter_") else chapter

        (RESULTS_DIR / f"{slug}_clairfin_metrics.json").write_text(
            json.dumps({"aggregate": aggregate, "per_question": scored_rows}, indent=2), encoding="utf-8"
        )
        markdown = render_markdown(aggregate)
        (RESULTS_DIR / f"{slug}_clairfin_metrics.md").write_text(markdown, encoding="utf-8")
        print(markdown)

    if len(all_aggregates) > 1:
        combined = render_combined_markdown(all_aggregates)
        (RESULTS_DIR / "all_chapters_clairfin_metrics.md").write_text(combined, encoding="utf-8")
        print(combined)

    print(f"Saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
