
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"
LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"

def main():
    runs = sorted(
        [p for p in RUNS_DIR.iterdir()
         if p.is_dir() and (p / "metadata.json").exists()],
        key=lambda p: p.name
    )

    if not runs:
        raise SystemExit("No experiment run found.")

    run_dir = runs[-1]
    metadata = json.loads(
        (run_dir / "metadata.json").read_text()
    )

    run_id = metadata["run_id"]
    trials = metadata["trials"]

    if len(trials) != metadata["trials_per_condition"]:
        raise SystemExit(
            "Latest run is incomplete. Finish the experiment first."
        )

    records = []

    if LOG_PATH.exists():
        for line in LOG_PATH.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("run_id") == run_id:
                    records.append(record)

    by_key = {
        (r.get("trial_number"), r.get("condition")): r
        for r in records
    }

    conditions = sorted({
        name
        for trial in trials
        for name in trial["conditions"]
    })

    summary = {
        "run_id": run_id,
        "planned_generations": metadata["planned_generations"],
        "conditions": {},
        "canonical_vs_reordered": {}
    }

    report = [
        "# Preliminary Research Analysis",
        "",
        f"Run ID: {run_id}",
        "",
        f"Model: {metadata['model']}",
        f"Temperature: {metadata['temperature']}",
        f"Trials per condition: {metadata['trials_per_condition']}",
        ""
    ]

    for condition in conditions:
        generated = 0
        completed = 0
        sql_failures = 0
        contract_failures = 0
        evaluation_failures = 0
        rule_counts = defaultdict(lambda: [0, 0])
        compliant = 0

        for trial in trials:
            number = trial["trial_number"]
            generation = trial["conditions"].get(condition, {})

            if generation.get("generation") == "completed":
                generated += 1

            record = by_key.get((number, condition))

            if record is None:
                continue

            status = record.get("status")

            if status == "sql_execution_failure":
                sql_failures += 1
                continue

            if status == "result_contract_failure":
                contract_failures += 1
                continue

            if status == "evaluation_failure":
                evaluation_failures += 1
                continue

            if status != "completed":
                continue

            completed += 1

            tests = record.get("tests", {})

            baseline_correct = (
                record.get("baseline", {}).get("correct", False)
            )

            rules_correct = (
                bool(tests)
                and all(v is True for v in tests.values())
            )

            fully_compliant = baseline_correct and rules_correct

            if fully_compliant:
                compliant += 1

            for rule, passed in tests.items():
                rule_counts[rule][1] += 1
                if passed is True:
                    rule_counts[rule][0] += 1

        summary["conditions"][condition] = {
            "generated": generated,
            "evaluation_completed": completed,
            "sql_execution_failures": sql_failures,
            "result_contract_failures": contract_failures,
            "evaluation_failures": evaluation_failures,
            "fully_compliant": compliant,
            "full_compliance_percent": (
                round(compliant / completed * 100, 2)
                if completed else None
            ),
            "rule_metrics": {
                rule: {
                    "passed": values[0],
                    "tested": values[1],
                    "pass_rate_percent": (
                        round(values[0] / values[1] * 100, 2)
                        if values[1] else None
                    )
                }
                for rule, values in rule_counts.items()
            }
        }

        item = summary["conditions"][condition]

        report.extend([
            f"## {condition}",
            f"- Generated: {generated}",
            f"- Evaluation completed: {completed}",
            f"- SQL execution failures: {sql_failures}",
            f"- Result contract failures: {contract_failures}",
            f"- Evaluator failures: {evaluation_failures}",
            f"- Fully compliant: {compliant}",
            f"- Compliance rate: {item['full_compliance_percent']}%",
            ""
        ])

    # Paired descriptive comparison of B and C
    paired = []

    for trial in trials:
        number = trial["trial_number"]
        b = by_key.get((number, "B_combined_rules"))
        c = by_key.get((number, "C_reordered_rules"))

        if not b or not c:
            continue

        if b.get("status") != "completed":
            continue

        if c.get("status") != "completed":
            continue

        b_pass = (
            b.get("baseline", {}).get("correct", False)
            and bool(b.get("tests"))
            and all(value is True for value in b["tests"].values())
        )

        c_pass = (
            c.get("baseline", {}).get("correct", False)
            and bool(c.get("tests"))
            and all(value is True for value in c["tests"].values())
        )

        paired.append({
            "trial": number,
            "canonical_compliant": b_pass,
            "reordered_compliant": c_pass
        })

    b_count = sum(x["canonical_compliant"] for x in paired)
    c_count = sum(x["reordered_compliant"] for x in paired)

    comparison = {
        "paired_trials": len(paired),
        "canonical_compliant": b_count,
        "reordered_compliant": c_count,
        "canonical_rate_percent": (
            round(b_count / len(paired) * 100, 2)
            if paired else None
        ),
        "reordered_rate_percent": (
            round(c_count / len(paired) * 100, 2)
            if paired else None
        ),
        "difference_percentage_points": (
            round((c_count - b_count) / len(paired) * 100, 2)
            if paired else None
        ),
        "paired_results": paired
    }

    summary["canonical_vs_reordered"] = comparison

    report.extend([
        "## Canonical vs reordered rules",
        f"- Paired trials: {comparison['paired_trials']}",
        f"- Canonical compliance: {comparison['canonical_rate_percent']}%",
        f"- Reordered compliance: {comparison['reordered_rate_percent']}%",
        f"- Difference (reordered minus canonical): "
        f"{comparison['difference_percentage_points']} percentage points",
        "",
        "## Limitations",
        "- This is a preliminary repeated-trial analysis.",
        "- Small sample sizes do not establish statistical significance.",
        "- SQL execution failures and result-contract failures are not "
        "counted as semantic interference.",
        "- Results apply to the tested model, prompt, schema and dataset."
    ])

    (run_dir / "metrics_summary.json").write_text(
        json.dumps(summary, indent=2)
    )

    (run_dir / "preliminary_research_report.md").write_text(
        "\n".join(report)
    )

    print("Analysis completed.")
    print("Run:", run_id)
    print("Conditions:", len(conditions))
    print("Paired B/C trials:", len(paired))
    print("Reports saved locally:", run_dir)


if __name__ == "__main__":
    main()

