
import json
import hashlib
import random
import subprocess
import sys

from datetime import datetime
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "experiments" / "conditions.json"
RESULTS = ROOT / "results" / "experiments"

client = OpenAI(
    base_url="http://127.0.0.1:8645/v1",
    api_key="test",
    timeout=120
)

SCHEMA = """
customers(
    customer_id, name, is_verified, is_deleted
)

orders(
    order_id, customer_id, order_date, status
)

order_items(
    item_id, order_id, product_name, quantity, unit_price
)

payments(
    payment_id, order_id, amount
)

refunds(
    refund_id, order_id, amount
)

Important:
- orders.amount does not exist.
- Revenue uses payments.amount.
- Refunds use refunds.amount.
- Join using order_id and customer_id.
- Never invent columns.
"""


def generate_sql(model, temperature, question, rules):

    prompt = f"""
You are an SQLite SQL generator.

Database schema:
{SCHEMA}

Business rules:
{chr(10).join(rules)}

Question:
{question}

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
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=temperature
    )

    sql = response.choices[0].message.content.strip()

    if not sql:
        raise ValueError("Model returned empty SQL.")

    return sql, prompt


def main():

    config = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )

    model = config["model"]
    temperature = config["temperature"]
    question = config["question"]
    conditions = config["conditions"]

    trials = int(config.get("trials_per_condition", 5))
    seed = int(config.get("random_seed", 20261124))

    if trials < 1:
        raise ValueError("Trials must be at least 1.")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    run_directory = RESULTS / run_id
    run_directory.mkdir(parents=True, exist_ok=False)

    rng = random.Random(seed)

    metadata = {
        "run_id": run_id,
        "model": model,
        "temperature": temperature,
        "question": question,
        "trials_per_condition": trials,
        "random_seed": seed,
        "condition_count": len(conditions),
        "planned_generations": trials * len(conditions),
        "trials": []
    }

    total_success = 0
    total_failure = 0

    for trial_number in range(1, trials + 1):

        condition_order = list(conditions.keys())
        rng.shuffle(condition_order)

        trial_record = {
            "trial_number": trial_number,
            "condition_order": condition_order,
            "conditions": {}
        }

        print(f"\n========== TRIAL {trial_number}/{trials} ==========")

        for condition_name in condition_order:

            rules = conditions[condition_name]

            print(f"\nCondition: {condition_name}")

            condition_record = {}

            sql_path = run_directory / (
                f"trial_{trial_number:02d}_{condition_name}.sql"
            )

            try:

                sql, prompt = generate_sql(
                    model,
                    temperature,
                    question,
                    rules
                )

                sql_path.write_text(sql, encoding="utf-8")

                condition_record["generation"] = "completed"
                condition_record["sql_file"] = sql_path.name
                condition_record["prompt_sha256"] = hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest()

                print("SQL generated and saved locally.")

            except Exception as error:

                condition_record["generation"] = "failed"
                condition_record["error_type"] = type(error).__name__

                condition_record["evaluation"] = "not_run"

                total_failure += 1
                trial_record["conditions"][condition_name] = condition_record
                print("Generation failed:", type(error).__name__)

                continue

            evaluation = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "rule_checker.py"),
                    str(sql_path),
                    condition_name,
                    run_id,
                    str(trial_number)
                ],
                cwd=ROOT
            )

            if evaluation.returncode == 0:
                condition_record["evaluation"] = "completed"
                total_success += 1
                print("Evaluation completed.")
            else:
                condition_record["evaluation"] = "failed"
                total_failure += 1
                print("Evaluation failed. See local evaluator log.")

            trial_record["conditions"][condition_name] = condition_record

        metadata["trials"].append(trial_record)

        (run_directory / "metadata.json").write_text(
            json.dumps(metadata, indent=4),
            encoding="utf-8"
        )

    metadata["successful_evaluations"] = total_success
    metadata["failed_conditions"] = total_failure

    (run_directory / "metadata.json").write_text(
        json.dumps(metadata, indent=4),
        encoding="utf-8"
    )

    print("\n========== EXPERIMENT FINISHED ==========")
    print("Run ID:", run_id)
    print("Planned generations:", trials * len(conditions))
    print("Successful evaluations:", total_success)
    print("Failed conditions:", total_failure)
    print("Results saved locally:", run_directory)


if __name__ == "__main__":
    main()
