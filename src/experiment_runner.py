import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time

from datetime import datetime
from pathlib import Path
from openai import OpenAI


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "experiments" / "conditions.json"
DATASET_DIR = ROOT / "results" / "datasets"
RESULTS_DIR = ROOT / "results" / "experiments"
LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"

CHECKPOINT_VERSION = 1

client = OpenAI(
    base_url="http://127.0.0.1:8645/v1",
    api_key="test",
    timeout=120
)


SCHEMAS = {
    "baseline": """
customers(
    customer_id INTEGER,
    name TEXT,
    is_verified INTEGER,
    is_deleted INTEGER
)

orders(
    order_id INTEGER,
    customer_id INTEGER,
    order_date TEXT,
    status TEXT
)

order_items(
    item_id INTEGER,
    order_id INTEGER,
    product_name TEXT,
    quantity INTEGER,
    unit_price REAL
)

payments(
    payment_id INTEGER,
    order_id INTEGER,
    amount REAL
)

refunds(
    refund_id INTEGER,
    order_id INTEGER,
    amount REAL
)
""",

    "expanded": """
customers(
    customer_id INTEGER,
    name TEXT,
    is_verified INTEGER,
    is_deleted INTEGER
)

orders(
    order_id INTEGER,
    customer_id INTEGER,
    order_date TEXT,
    status TEXT
)

categories(
    category_id INTEGER,
    category_name TEXT
)

products(
    product_id INTEGER,
    product_name TEXT,
    category_id INTEGER,
    unit_price REAL
)

order_items(
    item_id INTEGER,
    order_id INTEGER,
    product_id INTEGER,
    quantity INTEGER
)

payment_methods(
    payment_method_id INTEGER,
    method_name TEXT
)

payments(
    payment_id INTEGER,
    order_id INTEGER,
    payment_method_id INTEGER,
    amount REAL
)

refund_reasons(
    reason_id INTEGER,
    reason_name TEXT
)

refunds(
    refund_id INTEGER,
    order_id INTEGER,
    reason_id INTEGER,
    amount REAL
)
"""
}


def generate_sql(model, temperature, question, rules, profile):
    schema = SCHEMAS[profile]

    prompt = f"""
You are an SQLite SQL generator.

Database schema:
{schema}

Business rules:
{chr(10).join(rules)}

Question:
{question}

Common revenue definition (applies to every condition):
- Net revenue = SUM(payments.amount) - SUM(refunds.amount).
- Calculate net revenue across all orders in the database.

Important:
- Do not assume customer eligibility filters unless they are listed in the experimental business rules.
- Join tables using their declared relationships.
- Do not invent columns or tables.
- Do not calculate revenue using order_items.unit_price.
- Apply every supplied experimental business rule.

Return exactly one SQLite SELECT query.

OUTPUT REQUIREMENTS:
- Return exactly one row.
- Return exactly one column.
- The column must contain one numeric total.
- Do not use GROUP BY.
- Do not return customer or order details.
- Do not return text or explanations.
- Never reference columns that do not exist.

Do not explain.
Do not use Markdown.
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature
    )

    sql = response.choices[0].message.content.strip()

    if not sql:
        raise ValueError("Model returned empty SQL.")

    return sql, prompt


def atomic_save(metadata, run_directory):
    """Write metadata safely so an interrupted write cannot corrupt it."""
    target = run_directory / "metadata.json"
    temporary = run_directory / "metadata.json.tmp"

    temporary.write_text(
        json.dumps(metadata, indent=4),
        encoding="utf-8"
    )

    os.replace(temporary, target)


def make_key(record):
    """Create the evaluator identity key for one condition."""
    try:
        return (
            str(record.get("run_id")),
            str(record.get("schema_profile")),
            int(record.get("dataset_size")),
            int(record.get("trial_number")),
            str(record.get("condition"))
        )
    except (TypeError, ValueError):
        return None


def load_evaluator_index(run_id):
    """Read evaluator records belonging to this experiment only."""
    index = {}

    if not LOG_PATH.exists():
        return index

    with LOG_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            key = make_key(record)

            if key and key[0] == run_id:
                # Keep the latest record if an older run already
                # contains duplicate entries.
                index[key] = record

    return index


def config_snapshot(config, trials):
    """Capture the settings that must remain unchanged during resume."""
    return {
        "model": config["model"],
        "temperature": config["temperature"],
        "question": config["question"],
        "trials_per_condition": trials,
        "random_seed": config["random_seed"],
        "dataset_sizes": config["dataset_sizes"],
        "schema_profiles": config["schema_profiles"],
        "conditions": config["conditions"]
    }


def snapshot_hash(snapshot):
    serialized = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":")
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def find_trial(metadata, profile, size, trial_number):
    for trial in metadata["trials"]:
        if (
            trial["schema_profile"] == profile
            and int(trial["dataset_size"]) == int(size)
            and int(trial["trial_number"]) == int(trial_number)
        ):
            return trial

    return None


def update_counts(metadata, evaluator_index):
    run_id = metadata["run_id"]

    records = {
        key: record
        for key, record in evaluator_index.items()
        if key[0] == run_id
    }

    planned = int(metadata["planned_generations"])
    evaluated = len(records)

    if evaluated > planned:
        raise ValueError(
            f"Evaluator records ({evaluated}) exceed "
            f"planned generations ({planned})."
        )

    successful = sum(
        record.get("status") == "completed"
        for record in records.values()
    )

    failed_evaluations = evaluated - successful

    generation_failures = 0

    for trial in metadata["trials"]:
        profile = trial["schema_profile"]
        size = int(trial["dataset_size"])
        trial_number = int(trial["trial_number"])

        for condition_name, condition_record in trial["conditions"].items():
            key = (
                run_id,
                profile,
                size,
                trial_number,
                condition_name,
            )

            if (
                condition_record.get("generation") == "failed"
                and key not in records
            ):
                generation_failures += 1

    metadata["successful_evaluations"] = successful
    metadata["failed_evaluations"] = failed_evaluations
    metadata["generation_failures"] = generation_failures
    metadata["failed_conditions"] = (
        failed_evaluations + generation_failures
    )
    metadata["evaluated_generations"] = evaluated
    metadata["pending_generations"] = planned - evaluated

    assert (
        metadata["evaluated_generations"]
        + metadata["pending_generations"]
        == planned
    )

def main():
    parser = argparse.ArgumentParser(
        description="Run the SQL rule-interaction benchmark."
    )

    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Run one trial per dataset, schema and condition."
    )

    parser.add_argument(
        "--resume",
        type=str,
        help="Resume an existing run using its run ID."
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Additional SQL generation attempts after a failure."
    )

    args = parser.parse_args()

    if args.retries < 0 or args.retries > 10:
        parser.error("--retries must be between 0 and 10.")

    config = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )

    model = config["model"]
    temperature = config["temperature"]
    question = config["question"]
    conditions = config["conditions"]
    dataset_sizes = config["dataset_sizes"]
    schema_profiles = config["schema_profiles"]

    trials = int(config["trials_per_condition"])

    if args.pilot:
        trials = 1

    if trials < 1:
        raise ValueError("Trials must be at least 1.")

    condition_names = [item["name"] for item in conditions]

    if len(condition_names) != len(set(condition_names)):
        raise ValueError("Condition names must be unique.")

    for profile in schema_profiles:
        if profile not in SCHEMAS:
            raise ValueError(f"Unknown schema profile: {profile}")

    for profile in schema_profiles:
        for size in dataset_sizes:
            dataset_path = DATASET_DIR / f"{profile}_{size}_orders.db"

            if not dataset_path.exists():
                raise FileNotFoundError(
                    f"Dataset missing: {dataset_path}"
                )

    total_planned = (
        len(dataset_sizes)
        * len(schema_profiles)
        * len(conditions)
        * trials
    )

    snapshot = config_snapshot(config, trials)
    current_config_hash = snapshot_hash(snapshot)

    if args.resume:
        run_id = args.resume

        if (
            not run_id
            or Path(run_id).name != run_id
            or run_id in {".", ".."}
        ):
            raise ValueError("Invalid run ID.")

        run_directory = (RESULTS_DIR / run_id).resolve()

        if run_directory.parent != RESULTS_DIR.resolve():
            raise ValueError("Invalid run directory.")

        metadata_path = run_directory / "metadata.json"

        if not metadata_path.is_file():
            raise FileNotFoundError(
                f"Metadata not found for run: {run_id}"
            )

        metadata = json.loads(
            metadata_path.read_text(encoding="utf-8")
        )

        if metadata.get("checkpoint_version") != CHECKPOINT_VERSION:
            raise ValueError(
                "This run does not have a compatible checkpoint. "
                "Legacy runs, including the original pilot, "
                "cannot be resumed by this runner."
            )

        if metadata.get("config_sha256") != current_config_hash:
            raise ValueError(
                "Experiment configuration has changed. "
                "Restore the original configuration before resuming."
            )

        if metadata.get("planned_generations") != total_planned:
            raise ValueError("Planned generation count does not match.")

        print("Resuming existing experiment:", run_id)

    else:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        run_directory = RESULTS_DIR / run_id
        run_directory.mkdir(parents=False, exist_ok=False)

        metadata = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "run_id": run_id,
            "status": "running",
            "model": model,
            "temperature": temperature,
            "question": question,
            "trials_per_condition": trials,
            "random_seed": int(config["random_seed"]),
            "dataset_sizes": dataset_sizes,
            "schema_profiles": schema_profiles,
            "condition_count": len(conditions),
            "conditions": conditions,
            "planned_generations": total_planned,
            "pilot": args.pilot,
            "config_sha256": current_config_hash,
            "config_snapshot": snapshot,
            "trials": []
        }

        atomic_save(metadata, run_directory)

        print("Starting new experiment:", run_id)

    evaluator_index = load_evaluator_index(run_id)

    metadata["status"] = "running"
    atomic_save(metadata, run_directory)

    print("\n========== SQL RESEARCH BENCHMARK ==========")
    print("Run ID:", run_id)
    print("Model:", model)
    print("Planned generations:", total_planned)
    print("Additional generation retries:", args.retries)

    try:
        for profile in schema_profiles:
            for size in dataset_sizes:
                dataset_path = DATASET_DIR / f"{profile}_{size}_orders.db"

                print(
                    f"\n========== {profile.upper()} | "
                    f"{size} ORDERS =========="
                )

                for trial_number in range(1, trials + 1):
                    trial_record = find_trial(
                        metadata,
                        profile,
                        size,
                        trial_number
                    )

                    if trial_record is None:
                        condition_order = list(conditions)
                        seed_material = (
                            f"{int(config['random_seed'])}:"
                            f"{profile}:{size}:{trial_number}"
                        ).encode("utf-8")

                        trial_seed = int(
                            hashlib.sha256(seed_material).hexdigest(),
                            16,
                        )

                        trial_rng = random.Random(trial_seed)
                        trial_rng.shuffle(condition_order)

                        trial_record = {
                            "schema_profile": profile,
                            "dataset_size": size,
                            "trial_number": trial_number,
                            "condition_order": condition_order,
                            "conditions": {}
                        }

                        metadata["trials"].append(trial_record)
                        atomic_save(metadata, run_directory)

                    condition_order = trial_record["condition_order"]

                    for condition in condition_order:
                        condition_name = condition["name"]
                        rules = condition["rules"]

                        key = (
                            run_id,
                            profile,
                            int(size),
                            int(trial_number),
                            condition_name
                        )

                        sql_filename = (
                            f"{profile}_{size}_orders_"
                            f"trial_{trial_number:02d}_"
                            f"{condition_name}.sql"
                        )

                        sql_path = run_directory / sql_filename

                        condition_record = trial_record[
                            "conditions"
                        ].setdefault(condition_name, {})

                        # Recover an evaluation already written by
                        # rule_checker.py before a previous crash.
                        evaluator_record = evaluator_index.get(key)

                        if evaluator_record is not None:
                            condition_record["sql_file"] = sql_filename
                            condition_record["evaluation"] = (
                                "completed"
                                if evaluator_record.get("status") == "completed"
                                else "failed"
                            )
                            condition_record["evaluator_status"] = (
                                evaluator_record.get("status")
                            )
                            condition_record["recovered_from_log"] = True

                            atomic_save(metadata, run_directory)

                            print(
                                f"[RECOVERED] {condition_name}: "
                                f"{evaluator_record.get('status')}"
                            )
                            continue

                        print(
                            f"\n[{len(evaluator_index) + 1}/"
                            f"{total_planned}] {condition_name}"
                        )

                        # Reuse a generated SQL file if a crash
                        # happened after writing it but before evaluation.
                        if sql_path.is_file() and sql_path.stat().st_size > 0:
                            sql = sql_path.read_text(
                                encoding="utf-8"
                            ).strip()

                            if sql:
                                condition_record["generation"] = "completed"
                                condition_record["sql_file"] = sql_filename
                                condition_record["reused_sql_file"] = True
                            else:
                                sql_path.unlink()

                        if not sql_path.exists():
                            generation_succeeded = False

                            for attempt in range(args.retries + 1):
                                try:
                                    sql, prompt = generate_sql(
                                        model,
                                        temperature,
                                        question,
                                        rules,
                                        profile
                                    )

                                    sql_path.write_text(
                                        sql,
                                        encoding="utf-8"
                                    )

                                    condition_record["generation"] = "completed"
                                    condition_record["sql_file"] = sql_filename
                                    condition_record["prompt_sha256"] = (
                                        hashlib.sha256(
                                            prompt.encode("utf-8")
                                        ).hexdigest()
                                    )
                                    condition_record.pop("error_type", None)
                                    condition_record.pop("error", None)

                                    generation_succeeded = True

                                    print(
                                        f"SQL generated "
                                        f"(attempt {attempt + 1})."
                                    )
                                    break

                                except Exception as error:
                                    condition_record["generation"] = "failed"
                                    condition_record["error_type"] = (
                                        type(error).__name__
                                    )
                                    condition_record["error"] = str(error)

                                    atomic_save(metadata, run_directory)

                                    print(
                                        f"Generation attempt "
                                        f"{attempt + 1} failed: "
                                        f"{type(error).__name__}"
                                    )

                                    if attempt < args.retries:
                                        time.sleep(
                                            min(2 ** attempt, 30)
                                        )

                            if not generation_succeeded:
                                condition_record["evaluation"] = "not_run"
                                atomic_save(metadata, run_directory)
                                continue

                        # Save the SQL generation checkpoint before
                        # launching the evaluator.
                        atomic_save(metadata, run_directory)

                        evaluation = subprocess.run(
                            [
                                sys.executable,
                                str(ROOT / "src" / "rule_checker.py"),
                                str(sql_path),
                                condition_name,
                                run_id,
                                str(trial_number),
                                str(dataset_path),
                                profile,
                                str(size)
                            ],
                            cwd=ROOT
                        )

                        # The evaluator log is the source of truth.
                        evaluator_index = load_evaluator_index(run_id)
                        evaluator_record = evaluator_index.get(key)

                        if evaluator_record is not None:
                            evaluator_status = evaluator_record.get("status")

                            condition_record["evaluation"] = (
                                "completed"
                                if evaluator_status == "completed"
                                else "failed"
                            )
                            condition_record["evaluator_status"] = (
                                evaluator_status
                            )

                            if evaluation.returncode != 0:
                                condition_record["evaluator_returncode"] = (
                                    evaluation.returncode
                                )

                            print("Evaluator status:", evaluator_status)

                        else:
                            condition_record["evaluation"] = "failed"
                            condition_record["evaluator_status"] = (
                                "missing_evaluator_record"
                            )
                            condition_record["evaluator_returncode"] = (
                                evaluation.returncode
                            )

                            print(
                                "Evaluation failed: no evaluator record "
                                "was found."
                            )

                        update_counts(metadata, evaluator_index)
                        atomic_save(metadata, run_directory)

    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        update_counts(metadata, evaluator_index)
        atomic_save(metadata, run_directory)

        print("\nExperiment interrupted safely.")
        print("Resume with:")
        resume_command = (
            f"python src/experiment_runner.py --resume {run_id}"
        )

        if metadata.get("pilot"):
            resume_command += " --pilot"

        print(resume_command)
        raise SystemExit(130)

    update_counts(metadata, evaluator_index)

    if metadata["pending_generations"] == 0:
        if (
            metadata["failed_evaluations"] == 0
            and metadata["generation_failures"] == 0
        ):
            metadata["status"] = "completed"
        else:
            metadata["status"] = "completed_with_failures"
    else:
        metadata["status"] = "incomplete"

    atomic_save(metadata, run_directory)

    print("\n========== EXPERIMENT FINISHED ==========")
    print("Run ID:", run_id)
    print("Planned generations:", total_planned)
    print("Evaluated generations:", metadata["evaluated_generations"])
    print("Successful evaluations:", metadata["successful_evaluations"])
    print("Failed evaluations:", metadata["failed_evaluations"])
    print("Generation failures:", metadata["generation_failures"])
    print("Pending generations:", metadata["pending_generations"])
    print("Final status:", metadata["status"])
    print("Results saved locally:", run_directory)


if __name__ == "__main__":
    main()
