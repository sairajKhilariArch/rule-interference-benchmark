"""Aggregate final analysis for evaluator-v2 records; never prints SQL or DB contents."""
import argparse
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"
REEVALUATION_DIR = ROOT / "results" / "reevaluations"
VERSION = "2.0"
CONDITIONS = (
    "A_single_deleted_rule",
    "A_single_verified_rule",
    "A_single_join_rule",
    "B_combined_rules",
    "C_reordered_rules",
)


def absolute_percentage_error(expected, actual):
    expected, actual = float(expected), float(actual)
    if abs(expected) <= 1e-12:
        return None
    return abs(actual - expected) / abs(expected) * 100.0


def exact_sign_flip_p_value(differences):
    """Two-sided exact paired sign-flip test for the mean paired difference."""
    values = [float(x) for x in differences if abs(float(x)) > 1e-12]
    n = len(values)
    if n == 0:
        return 1.0
    if n > 20:
        raise ValueError("Exact sign-flip test supports at most 20 non-tied pairs.")
    observed = abs(sum(values))
    tolerance = max(1e-12, observed * 1e-12)
    extreme = 0
    total = 1 << n
    for signs in itertools.product((-1.0, 1.0), repeat=n):
        permuted = abs(sum(s * x for s, x in zip(signs, values)))
        if permuted + tolerance >= observed:
            extreme += 1
    return extreme / total


def holm_correction(p_values):
    if not p_values:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [None] * len(indexed)
    running_max = 0.0
    total = len(indexed)
    for rank, (original_index, p_value) in enumerate(indexed):
        running_max = max(running_max, min(1.0, (total - rank) * p_value))
        adjusted[original_index] = running_max
    return adjusted


def _error(record):
    baseline = record.get("baseline") or {}
    return absolute_percentage_error(baseline.get("expected"), baseline.get("actual"))


def _condition_metrics(records):
    completed = [r for r in records if r.get("status") == "completed"]
    errors = [x for x in (_error(r) for r in completed) if x is not None]
    correct = sum(r.get("baseline", {}).get("correct") is True for r in completed)
    above = below = equal = 0
    rule_counts = defaultdict(lambda: [0, 0])
    hashes = {r.get("sql_sha256") for r in completed if r.get("sql_sha256")}
    compliant = 0

    for record in completed:
        baseline = record["baseline"]
        delta = float(baseline["actual"]) - float(baseline["expected"])
        if math.isclose(delta, 0.0, rel_tol=1e-9, abs_tol=1e-6):
            equal += 1
        elif delta > 0:
            above += 1
        else:
            below += 1

        tests = record.get("tests") or {}
        if baseline.get("correct") is True and tests and all(v is True for v in tests.values()):
            compliant += 1
        for rule, passed in tests.items():
            rule_counts[rule][0 if passed is True else 1] += 1

    return {
        "records": len(records),
        "completed": len(completed),
        "failed_evaluations": len(records) - len(completed),
        "baseline_correct": correct,
        "baseline_incorrect": len(completed) - correct,
        "baseline_accuracy_percent": round(100 * correct / len(completed), 2) if completed else None,
        "fully_compliant": compliant,
        "full_compliance_percent": round(100 * compliant / len(completed), 2) if completed else None,
        "actual_above_reference": above,
        "actual_below_reference": below,
        "actual_equal_reference": equal,
        "mean_absolute_percentage_error": round(mean(errors), 4) if errors else None,
        "median_absolute_percentage_error": round(median(errors), 4) if errors else None,
        "unique_sql_outputs": len(hashes),
        "repeated_sql_outputs": len(completed) - len(hashes),
        "rule_tests": {
            rule: {
                "passed": values[0],
                "failed": values[1],
                "tested": sum(values),
                "pass_rate_percent": round(100 * values[0] / sum(values), 2) if sum(values) else None,
            }
            for rule, values in sorted(rule_counts.items())
        },
    }


def _paired_comparison(records):
    by_trial = defaultdict(dict)
    for r in records:
        if r.get("condition") in {"B_combined_rules", "C_reordered_rules"}:
            by_trial[int(r["trial_number"])][r["condition"]] = r

    paired = []
    for trial, pair in sorted(by_trial.items()):
        b, c = pair.get("B_combined_rules"), pair.get("C_reordered_rules")
        if not b or not c or b.get("status") != "completed" or c.get("status") != "completed":
            continue
        b_error, c_error = _error(b), _error(c)
        if b_error is None or c_error is None:
            continue
        paired.append({
            "trial_number": trial,
            "combined_error_percent": b_error,
            "reordered_error_percent": c_error,
            "combined_minus_reordered_pp": b_error - c_error,
            "combined_sql_hash": b.get("sql_sha256"),
            "reordered_sql_hash": c.get("sql_sha256"),
        })

    differences = [x["combined_minus_reordered_pp"] for x in paired]
    non_tied = [x for x in differences if abs(x) > 1e-12]
    return {
        "paired_trials": len(paired),
        "combined_worse": sum(x > 1e-12 for x in differences),
        "reordered_worse": sum(x < -1e-12 for x in differences),
        "equal_error": len(differences) - len(non_tied),
        "different_error_pairs": len(non_tied),
        "identical_sql_pairs": sum(
            x["combined_sql_hash"] == x["reordered_sql_hash"]
            and x["combined_sql_hash"] is not None for x in paired
        ),
        "mean_combined_mape_percent": round(mean(x["combined_error_percent"] for x in paired), 4) if paired else None,
        "mean_reordered_mape_percent": round(mean(x["reordered_error_percent"] for x in paired), 4) if paired else None,
        "mean_combined_minus_reordered_pp": round(mean(differences), 4) if differences else None,
        "median_combined_minus_reordered_pp": round(median(differences), 4) if differences else None,
        "exact_sign_flip_p_value": exact_sign_flip_p_value(differences),
    }


def analyze_run(run_id):
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Invalid run ID.")

    run_dir = RUNS_DIR / run_id
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError("Original experiment metadata is missing.")
    output_dir = REEVALUATION_DIR / run_id
    log_path = output_dir / f"{run_id}_evaluator_v{VERSION}.jsonl"
    if not log_path.is_file():
        raise FileNotFoundError("Evaluator-v2 JSONL is missing.")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    records = []
    with log_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON on evaluator log line {line_number}.") from error

    planned = int(metadata["planned_generations"])
    if len(records) != planned:
        raise ValueError(f"Expected {planned} records; found {len(records)}.")

    expected_keys = set()
    for trial in metadata.get("trials", []):
        for condition in CONDITIONS:
            expected_keys.add((
                trial["schema_profile"], int(trial["dataset_size"]),
                int(trial["trial_number"]), condition
            ))

    indexed = {}
    for r in records:
        if r.get("source_run_id") != run_id or r.get("evaluator_version") != VERSION:
            raise ValueError("Unexpected source run or evaluator version.")
        key = (r.get("schema_profile"), int(r["dataset_size"]), int(r["trial_number"]), r.get("condition"))
        if key in indexed:
            raise ValueError(f"Duplicate evaluator identity: {key}")
        indexed[key] = r

    if set(indexed) != expected_keys:
        raise ValueError("Evaluator records do not match the complete experiment matrix.")

    statuses = Counter(r.get("status", "missing_status") for r in records)
    groups, paired = {}, {}
    p_values, p_keys = [], []

    for profile in metadata["schema_profiles"]:
        for size in metadata["dataset_sizes"]:
            selected = [
                r for r in records
                if r["schema_profile"] == profile and int(r["dataset_size"]) == int(size)
            ]
            condition_metrics = {}
            for condition in CONDITIONS:
                condition_metrics[condition] = _condition_metrics([
                    r for r in selected if r["condition"] == condition
                ])
            name = f"{profile}_{size}"
            groups[name] = {
                "schema_profile": profile,
                "dataset_size": int(size),
                "conditions": condition_metrics,
            }
            comparison = _paired_comparison(selected)
            paired[name] = comparison
            p_keys.append(name)
            p_values.append(comparison["exact_sign_flip_p_value"])

    for name, adjusted in zip(p_keys, holm_correction(p_values)):
        paired[name]["holm_adjusted_p_value"] = adjusted
        paired[name]["significant_after_holm_0_05"] = adjusted <= 0.05

    completed = [r for r in records if r.get("status") == "completed"]
    errors = [x for x in (_error(r) for r in completed) if x is not None]
    correct = sum(r.get("baseline", {}).get("correct") is True for r in completed)
    fully_compliant = sum(
        r.get("baseline", {}).get("correct") is True
        and bool(r.get("tests"))
        and all(v is True for v in r["tests"].values())
        for r in completed
    )

    result = {
        "source_run_id": run_id,
        "evaluator_version": VERSION,
        "model": metadata.get("model"),
        "temperature": metadata.get("temperature"),
        "trials_per_condition": metadata.get("trials_per_condition"),
        "planned_generations": planned,
        "evaluator_status_counts": dict(sorted(statuses.items())),
        "overall": {
            "records": len(records),
            "completed": len(completed),
            "baseline_correct": correct,
            "baseline_accuracy_percent": round(100 * correct / len(completed), 2) if completed else None,
            "fully_compliant": fully_compliant,
            "full_compliance_percent": round(100 * fully_compliant / len(completed), 2) if completed else None,
            "mean_absolute_percentage_error": round(mean(errors), 4) if errors else None,
            "median_absolute_percentage_error": round(median(errors), 4) if errors else None,
        },
        "groups": groups,
        "paired_rule_order_analysis": paired,
        "statistical_method": (
            "Two-sided exact paired sign-flip permutation test on B-minus-C "
            "per-query absolute percentage error; Holm-Bonferroni correction "
            "across six schema-size comparisons."
        ),
        "significance_level": 0.05,
        "limitations": [
            "The reference calculator includes all order statuses; the prompt uses 'reportable' without defining status.",
            "The prompt says not to use GROUP BY while the join rule asks for per-order aggregation; this may constrain implementation.",
            "The configured random seed shuffles condition order; it is not passed as a generation seed to the model API.",
            "Repeated SQL hashes indicate repeated outputs and weaken independence assumptions.",
            "Ten trials per condition provide limited statistical power.",
            "There is no no-rule baseline condition.",
            "Results apply only to this model, prompt, schema, datasets, and evaluator version.",
        ],
    }

    report = [
        "# RuleBench — Final Evaluator V2 Analysis",
        "",
        f"- Source experiment: {run_id}",
        f"- Evaluator version: {VERSION}",
        f"- Model: {metadata.get('model')}",
        f"- Temperature: {metadata.get('temperature')}",
        f"- Trials per condition: {metadata.get('trials_per_condition')}",
        f"- Planned generations: {planned}",
        "",
        "## 1. Evaluation integrity",
        "",
        "| Evaluator status | Records |",
        "|---|---:|",
    ]
    for status, count in sorted(statuses.items()):
        report.append(f"| {status} | {count} |")
    report.extend([
        "",
        f"- Completed evaluations: {len(completed)}/{len(records)}",
        f"- Baseline-correct queries: {correct}/{len(completed)}",
        f"- Fully compliant queries: {fully_compliant}/{len(completed)}",
        f"- Overall mean absolute percentage error: {result['overall']['mean_absolute_percentage_error']}%",
        f"- Overall median absolute percentage error: {result['overall']['median_absolute_percentage_error']}%",
        "",
        "## 2. Condition results by schema and dataset size",
        "",
        "| Schema | Orders | Condition | Completed | Baseline correct | Fully compliant | MAPE | Unique SQL outputs |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ])
    for group in groups.values():
        for condition, m in group["conditions"].items():
            report.append(
                f"| {group['schema_profile']} | {group['dataset_size']} | {condition} | "
                f"{m['completed']} | {m['baseline_correct']} | {m['fully_compliant']} | "
                f"{m['mean_absolute_percentage_error']}% | {m['unique_sql_outputs']} |"
            )

    report.extend([
        "",
        "## 3. Mutation-test results",
        "",
        "| Schema | Orders | Condition | Rule | Passed | Tested | Pass rate |",
        "|---|---:|---|---|---:|---:|---:|",
    ])
    for group in groups.values():
        for condition, m in group["conditions"].items():
            for rule, counts in m["rule_tests"].items():
                report.append(
                    f"| {group['schema_profile']} | {group['dataset_size']} | {condition} | "
                    f"{rule} | {counts['passed']} | {counts['tested']} | "
                    f"{counts['pass_rate_percent']}% |"
                )

    report.extend([
        "",
        "## 4. Paired canonical-versus-reordered error analysis",
        "",
        "Positive B-minus-C values mean combined-rule error was higher; negative values mean reordered-rule error was higher.",
        "",
        "| Schema | Orders | Pairs | B worse | C worse | Tied | Mean B−C (pp) | Exact p | Holm p | Significant? |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for name, comparison in paired.items():
        group = groups[name]
        report.append(
            f"| {group['schema_profile']} | {group['dataset_size']} | {comparison['paired_trials']} | "
            f"{comparison['combined_worse']} | {comparison['reordered_worse']} | {comparison['equal_error']} | "
            f"{comparison['mean_combined_minus_reordered_pp']} | "
            f"{comparison['exact_sign_flip_p_value']:.6g} | "
            f"{comparison['holm_adjusted_p_value']:.6g} | "
            f"{comparison['significant_after_holm_0_05']} |"
        )

    report.extend([
        "",
        "## 5. Interpretation",
        "",
        f"- The evaluator processed {len(records)} records; {len(completed)} completed and {len(records) - len(completed)} did not complete.",
        f"- Baseline correctness was {correct}/{len(completed)}. The resulting zero full-compliance rate creates a floor effect for binary compliance comparisons.",
        "- Paired percentage error is more informative than binary compliance for this run, but remains dependent on the reference definition.",
        "- Exact paired sign-flip tests were corrected across six comparisons using Holm-Bonferroni.",
        "- A non-significant result does not prove that rule order has no effect.",
        "",
        "## 6. Methodological limitations",
        "",
        "- The reference includes completed, pending, and cancelled orders. The prompt uses 'reportable' without defining order status.",
        "- The prompt says 'Do not use GROUP BY' while the join rule asks for per-order aggregation. This may constrain the model. Do not silently change the completed run; clarify this instruction in a future experiment.",
        "- The configured seed controls condition execution order; the model API call does not receive a generation seed.",
        "- Check unique SQL-output counts before treating ten nominal trials as ten independent generations. Temperature is zero, so repeated outputs are possible.",
        "- There is no no-rule baseline condition.",
        "- Findings apply only to the tested model, prompt, schemas, synthetic datasets, and evaluator version.",
        "",
        "## 7. Reproducibility",
        "",
        "Generated from evaluator-v2 records and original experiment metadata. This script does not open or print generated SQL or database contents and does not modify original experiment files or logs.",
    ])

    (output_dir / "final_metrics_v2.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (output_dir / "final_research_report_v2.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print("Final analysis completed.")
    print("Source run:", run_id)
    print("Evaluator records:", len(records))
    print("Completed:", len(completed))
    print("Baseline correct:", correct)
    print("Fully compliant:", fully_compliant)
    print("Paired comparisons:", len(paired))
    print("Aggregate outputs saved locally:")
    print(output_dir / "final_metrics_v2.json")
    print(output_dir / "final_research_report_v2.md")


def main():
    parser = argparse.ArgumentParser(description="Analyze evaluator-v2 aggregate results without displaying SQL.")
    parser.add_argument("--run-id", required=True)
    analyze_run(parser.parse_args().run_id)


if __name__ == "__main__":
    main()
