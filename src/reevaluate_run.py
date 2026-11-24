"""Re-evaluate saved SQL with the current evaluator without changing old logs."""

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src import rule_checker


ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"
DATASET_DIR = ROOT / "results" / "datasets"
OUTPUT_DIR = ROOT / "results" / "reevaluations"
EVALUATOR_VERSION = "2.0"


def safe_child(parent, relative_path):
    """Resolve a metadata path and reject paths outside the run directory."""
    candidate = (parent / relative_path).resolve()
    if candidate.parent != parent.resolve():
        raise ValueError("SQL path escapes the experiment run directory.")
    return candidate


def evaluate_saved_run(run_id, overwrite=False):
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Invalid run ID.")

    run_dir = (RUNS_DIR / run_id).resolve()
    if run_dir.parent != RUNS_DIR.resolve():
        raise ValueError("Invalid experiment run directory.")

    metadata_path = run_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Metadata not found for run: {run_id}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / f"{run_id}_evaluator_v{EVALUATOR_VERSION}.jsonl"
    summary_path = output_dir / f"{run_id}_evaluator_v{EVALUATOR_VERSION}_summary.json"

    if not overwrite and (log_path.exists() or summary_path.exists()):
        raise FileExistsError(
            "A re-evaluation already exists. Use --overwrite only if you "
            "intend to replace that separate re-evaluation."
        )

    records = []
    for trial in metadata.get("trials", []):
        profile = trial.get("schema_profile")
        size = int(trial.get("dataset_size"))
        trial_number = int(trial.get("trial_number"))
        dataset_path = DATASET_DIR / f"{profile}_{size}_orders.db"

        for condition, condition_data in trial.get("conditions", {}).items():
            sql_name = condition_data.get("sql_file")
            record = {
                "timestamp": datetime.now().isoformat(),
                "evaluator_version": EVALUATOR_VERSION,
                "source_run_id": run_id,
                "run_id": f"{run_id}_reeval_v{EVALUATOR_VERSION}",
                "schema_profile": profile,
                "dataset_size": size,
                "trial_number": trial_number,
                "condition": condition,
                "status": "pending",
                "baseline": None,
                "tests": {},
                "error": None,
            }

            try:
                if not sql_name:
                    raise FileNotFoundError("No saved SQL filename in metadata.")

                sql_path = safe_child(run_dir, sql_name)
                if not sql_path.is_file():
                    raise FileNotFoundError("Saved SQL file is missing.")
                if not dataset_path.is_file():
                    raise FileNotFoundError("Dataset file is missing.")

                sql = sql_path.read_text(encoding="utf-8").strip()
                if not sql.upper().startswith(("SELECT", "WITH")):
                    raise ValueError("Only SELECT queries are supported.")

                record["sql_sha256"] = hashlib.sha256(
                    sql.encode("utf-8")
                ).hexdigest()
                record["sql_file"] = sql_name
                record["database"] = str(dataset_path)
                record["baseline"] = rule_checker.evaluate_baseline(
                    sql, condition, dataset_path
                )
                record["tests"] = rule_checker.evaluate_rules(
                    sql, condition, dataset_path
                )
                record["status"] = "completed"

            except sqlite3.Error as error:
                record["status"] = "sql_execution_failure"
                record["error"] = str(error)
            except rule_checker.ResultContractError as error:
                record["status"] = "result_contract_failure"
                record["error"] = str(error)
            except FileNotFoundError as error:
                record["status"] = "missing_input"
                record["error"] = str(error)
            except Exception as error:
                record["status"] = "evaluation_failure"
                record["error"] = f"{type(error).__name__}: {error}"

            records.append(record)

    if not records:
        raise ValueError("No saved SQL conditions were found in this run.")

    counts = defaultdict(lambda: {
        "records": 0,
        "completed": 0,
        "baseline_correct": 0,
        "baseline_incorrect": 0,
        "failed": 0,
        "rule_tests": defaultdict(lambda: {"passed": 0, "failed": 0}),
    })

    status_counts = defaultdict(int)
    for record in records:
        status_counts[record["status"]] += 1
        group = counts[(record["schema_profile"], record["dataset_size"],
                        record["condition"])]
        group["records"] += 1

        if record["status"] != "completed":
            group["failed"] += 1
            continue

        group["completed"] += 1
        if record["baseline"]["correct"]:
            group["baseline_correct"] += 1
        else:
            group["baseline_incorrect"] += 1

        for rule_name, passed in record["tests"].items():
            target = group["rule_tests"][rule_name]
            target["passed" if passed is True else "failed"] += 1

    summary_groups = {}
    for key, values in counts.items():
        profile, size, condition = key
        summary_groups[f"{profile}_{size}_{condition}"] = {
            **{k: v for k, v in values.items() if k != "rule_tests"},
            "rule_tests": dict(values["rule_tests"]),
        }

    summary = {
        "source_run_id": run_id,
        "reevaluation_run_id": f"{run_id}_reeval_v{EVALUATOR_VERSION}",
        "evaluator_version": EVALUATOR_VERSION,
        "created_at": datetime.now().isoformat(),
        "records": len(records),
        "status_counts": dict(status_counts),
        "groups": summary_groups,
        "note": (
            "Separate re-evaluation. Original experiment metadata and "
            "rule_test_log.jsonl were not modified."
        ),
    }

    temporary_log = log_path.with_suffix(".jsonl.tmp")
    temporary_summary = summary_path.with_suffix(".json.tmp")

    temporary_log.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )
    temporary_summary.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_log, log_path)
    os.replace(temporary_summary, summary_path)

    print("Re-evaluation completed.")
    print("Source run:", run_id)
    print("Records:", len(records))
    for status, count in sorted(status_counts.items()):
        print(f"{status}: {count}")
    print("Aggregate summary:", summary_path)
    print("Separate evaluator log:", log_path)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Re-evaluate saved SQL using the current evaluator. "
            "Does not change the original experiment log."
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace this separate re-evaluation output if it already exists.",
    )
    args = parser.parse_args()
    evaluate_saved_run(args.run_id, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
