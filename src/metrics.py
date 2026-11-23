
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent.parent

LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"
OUTPUT_PATH = ROOT / "results" / "metrics_summary.json"


def load_records():
    records = []

    if not LOG_PATH.exists():
        raise FileNotFoundError("Evaluation log not found.")

    with LOG_PATH.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            record = json.loads(line)

            # Ignore records from older evaluator versions.
            if "status" in record:
                records.append(record)

    return records


def calculate_metrics(records):
    statuses = Counter(
        record["status"] for record in records
    )

    completed = [
        record for record in records
        if record["status"] == "completed"
    ]

    total = len(records)
    completed_count = len(completed)

    metrics = {
        "total_runs": total,
        "completed_runs": completed_count,
        "sql_execution_failures": statuses["sql_execution_failure"],
        "evaluation_failures": statuses["evaluation_failure"],
        "evaluation_completion_rate": (
            completed_count / total * 100 if total else 0
        ),
        "rule_metrics": {}
    }

    rules = [
        "deleted_customer_rule",
        "verified_customer_rule",
        "refund_rule",
        "join_multiplication_rule"
    ]

    violating_runs = 0

    for rule in rules:
        tested = [
            record["tests"][rule]
            for record in completed
            if rule in record.get("tests", {})
        ]

        passed = sum(value is True for value in tested)
        failed = sum(value is False for value in tested)

        metrics["rule_metrics"][rule] = {
            "tested": len(tested),
            "passed": passed,
            "failed": failed,
            "pass_rate_percent": (
                passed / len(tested) * 100 if tested else None
            )
        }

    for record in completed:
        tests = record.get("tests", {})

        if any(value is False for value in tests.values()):
            violating_runs += 1

    metrics["executable_rule_violation_rate"] = (
        violating_runs / completed_count * 100
        if completed_count else None
    )

    return metrics


def main():
    records = load_records()
    metrics = calculate_metrics(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(metrics, file, indent=4)

    print("Metrics calculation completed.")
    print("Metrics saved locally.")


if __name__ == "__main__":
    main()
