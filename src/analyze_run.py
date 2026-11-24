
import json
from pathlib import Path
from collections import defaultdict


ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"
LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"


def load_records(run_id):
    """Load evaluator records belonging to one experiment run."""

    records = []

    if not LOG_PATH.exists():
        return records

    with LOG_PATH.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            record = json.loads(line)

            if record.get("run_id") == run_id:
                records.append(record)

    return records


def is_fully_compliant(record):
    """Check both independent correctness and all assigned rules."""

    if record.get("status") != "completed":
        return False

    baseline_correct = (
        record.get("baseline", {}).get("correct", False)
    )

    tests = record.get("tests", {})

    return (
        baseline_correct
        and bool(tests)
        and all(value is True for value in tests.values())
    )


def summarize_condition(condition_name, trials, records):
    """Calculate evaluation and rule metrics for one condition."""

    generated = 0
    completed = 0

    sql_failures = 0
    contract_failures = 0
    evaluation_failures = 0

    compliant = 0
    rule_counts = defaultdict(lambda: [0, 0])

    for trial in trials:

        generation = trial["conditions"].get(
            condition_name, {}
        )

        if generation.get("generation") == "completed":
            generated += 1

        record = records.get(
            (trial["trial_number"], condition_name)
        )

        if record is None:
            continue

        status = record.get("status")

        if status == "sql_execution_failure":
            sql_failures += 1
            continue

        if status == "result_contract_failure":
            contract_failures += 1
            continue

        if status != "completed":
            evaluation_failures += 1
            continue

        completed += 1

        if is_fully_compliant(record):
            compliant += 1

        for rule, passed in record.get("tests", {}).items():
            rule_counts[rule][1] += 1

            if passed is True:
                rule_counts[rule][0] += 1

    return {
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


def compare_canonical_reordered(trials, records):
    """Compare B and C using matching trial pairs."""

    paired = []

    for trial in trials:

        number = trial["trial_number"]

        b = records.get((number, "B_combined_rules"))
        c = records.get((number, "C_reordered_rules"))

        if not b or not c:
            continue

        if b.get("status") != "completed":
            continue

        if c.get("status") != "completed":
            continue

        paired.append({
            "trial": number,
            "canonical_compliant": is_fully_compliant(b),
            "reordered_compliant": is_fully_compliant(c)
        })

    b_count = sum(
        item["canonical_compliant"] for item in paired
    )

    c_count = sum(
        item["reordered_compliant"] for item in paired
    )

    count = len(paired)

    planned_count = len(trials)

    b_statuses = [
        records.get((trial["trial_number"], "B_combined_rules"), {}).get("status")
        for trial in trials
    ]
    c_statuses = [
        records.get((trial["trial_number"], "C_reordered_rules"), {}).get("status")
        for trial in trials
    ]

    b_completed = sum(s == "completed" for s in b_statuses)
    c_completed = sum(s == "completed" for s in c_statuses)

    both_completed = sum(
        b == "completed" and c == "completed"
        for b, c in zip(b_statuses, c_statuses)
    )

    b_only = sum(
        b == "completed" and c != "completed"
        for b, c in zip(b_statuses, c_statuses)
    )
    c_only = sum(
        b != "completed" and c == "completed"
        for b, c in zip(b_statuses, c_statuses)
    )
    neither = sum(
        b != "completed" and c != "completed"
        for b, c in zip(b_statuses, c_statuses)
    )

    return {
        "planned_trials": planned_count,
        "canonical_completed": b_completed,
        "reordered_completed": c_completed,
        "canonical_completion_rate_percent": (
            round(b_completed / planned_count * 100, 2)
            if planned_count else None
        ),
        "reordered_completion_rate_percent": (
            round(c_completed / planned_count * 100, 2)
            if planned_count else None
        ),
        "both_completed": both_completed,
        "canonical_only_completed": b_only,
        "reordered_only_completed": c_only,
        "neither_completed": neither,
        "paired_trials": count,
        "canonical_compliant": b_count,
        "reordered_compliant": c_count,
        "canonical_rate_percent": (
            round(b_count / count * 100, 2)
            if count else None
        ),
        "reordered_rate_percent": (
            round(c_count / count * 100, 2)
            if count else None
        ),
        "difference_percentage_points": (
            round((c_count - b_count) / count * 100, 2)
            if count else None
        ),
        "paired_results": paired
    }


def main():

    runs = sorted(
        [
            path for path in RUNS_DIR.iterdir()
            if path.is_dir()
            and (path / "metadata.json").exists()
        ],
        key=lambda path: path.name
    )

    if not runs:
        raise SystemExit("No experiment run found.")

    run_dir = runs[-1]

    metadata = json.loads(
        (run_dir / "metadata.json").read_text(
            encoding="utf-8"
        )
    )

    run_status = metadata.get("status")

    if run_status not in {"completed", "completed_with_failures"}:
        raise SystemExit(
            f"Refusing analysis: experiment status is {run_status!r}"
        )

    planned = metadata.get("planned_generations")
    evaluated = metadata.get("evaluated_generations")
    pending = metadata.get("pending_generations")

    if planned is not None and evaluated != planned:
        raise SystemExit(
            f"Refusing analysis: evaluated {evaluated} of {planned}"
        )

    if pending not in (None, 0):
        raise SystemExit(
            f"Refusing analysis: {pending} generations remain pending"
        )

    run_id = metadata["run_id"]
    trials = metadata["trials"]

    dataset_sizes = metadata["dataset_sizes"]
    schema_profiles = metadata["schema_profiles"]

    conditions = [
        condition["name"]
        for condition in metadata.get("conditions", [])
    ]

    # Fallback for metadata generated by the current runner,
    # which does not yet store the full condition definitions.
    if not conditions:
        conditions = [
            "A_single_deleted_rule",
            "A_single_verified_rule",
            "A_single_join_rule",
            "B_combined_rules",
            "C_reordered_rules"
        ]

    evaluator_records = load_records(run_id)

    # Index records by their complete experiment identity.
    records_by_group = defaultdict(dict)

    for record in evaluator_records:

        profile = record.get("schema_profile")
        size = record.get("dataset_size")
        trial_number = record.get("trial_number")
        condition = record.get("condition")

        if (
            profile is None
            or size is None
            or trial_number is None
            or condition is None
        ):
            continue

        key = (profile, int(size))

        record_key = (int(trial_number), condition)

        if record_key in records_by_group[key]:
            raise SystemExit(
                "Duplicate evaluator record detected: "
                f"group={key}, trial={trial_number}, "
                f"condition={condition}"
            )

        records_by_group[key][record_key] = record

    summary = {
        "run_id": run_id,
        "model": metadata["model"],
        "temperature": metadata["temperature"],
        "trials_per_condition": metadata["trials_per_condition"],
        "planned_generations": metadata["planned_generations"],
        "dataset_sizes": dataset_sizes,
        "schema_profiles": schema_profiles,
        "groups": {}
    }

    report = [
        "# Execution-Grounded SQL Research Analysis",
        "",
        f"Run ID: {run_id}",
        f"Model: {metadata['model']}",
        f"Temperature: {metadata['temperature']}",
        f"Trials per condition: {metadata['trials_per_condition']}",
        f"Planned generations: {metadata['planned_generations']}",
        ""
    ]

    for profile in schema_profiles:

        for size in dataset_sizes:

            group_key = (profile, int(size))

            group_trials = [
                trial for trial in trials
                if (
                    trial.get("schema_profile") == profile
                    and int(trial.get("dataset_size", -1)) == int(size)
                )
            ]

            group_records = records_by_group[group_key]

            group_summary = {
                "schema_profile": profile,
                "dataset_size": size,
                "trials_found": len(group_trials),
                "conditions": {},
                "canonical_vs_reordered": {}
            }

            report.extend([
                f"# {profile.upper()} — {size} orders",
                "",
                f"Trials found: {len(group_trials)}",
                ""
            ])

            for condition in conditions:

                metrics = summarize_condition(
                    condition,
                    group_trials,
                    group_records
                )

                group_summary["conditions"][condition] = metrics

                report.extend([
                    f"## {condition}",
                    f"- Generated: {metrics['generated']}",
                    f"- Evaluation completed: "
                    f"{metrics['evaluation_completed']}",
                    f"- SQL failures: "
                    f"{metrics['sql_execution_failures']}",
                    f"- Contract failures: "
                    f"{metrics['result_contract_failures']}",
                    f"- Evaluator failures: "
                    f"{metrics['evaluation_failures']}",
                    f"- Fully compliant: "
                    f"{metrics['fully_compliant']}",
                    f"- Compliance rate: "
                    f"{metrics['full_compliance_percent']}%",
                    ""
                ])

            comparison = compare_canonical_reordered(
                group_trials,
                group_records
            )

            group_summary["canonical_vs_reordered"] = comparison

            report.extend([
                "## Canonical vs reordered rules",
                f"- Paired trials: {comparison['paired_trials']}",
                f"- Canonical compliance: "
                f"{comparison['canonical_rate_percent']}%",
                f"- Reordered compliance: "
                f"{comparison['reordered_rate_percent']}%",
                f"- Difference: "
                f"{comparison['difference_percentage_points']} percentage points",
                ""
            ])

            summary["groups"][
                f"{profile}_{size}_orders"
            ] = group_summary

    report.extend([
        "# Limitations",
        "- This is a descriptive, execution-grounded analysis.",
        "- Statistical significance is not established by compliance rates alone.",
        "- Failed SQL execution and result-contract checks are reported separately.",
        "- Results apply to the tested model, prompts, schemas and datasets.",
        "- Each schema and dataset-size combination is analyzed independently."
    ])

    (run_dir / "metrics_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8"
    )

    (run_dir / "preliminary_research_report.md").write_text(
        "\n".join(report),
        encoding="utf-8"
    )

    print("Analysis completed.")
    print("Run:", run_id)
    print("Groups:", len(summary["groups"]))

    for group_name, group in summary["groups"].items():
        print(
            group_name,
            "| B/C paired trials:",
            group["canonical_vs_reordered"]["paired_trials"]
        )

    print("Reports saved locally:", run_dir)


if __name__ == "__main__":
    main()
