
import json
import hashlib
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
    api_key="test"
)

SCHEMA = """
Database tables and columns:

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

IMPORTANT:
- orders.amount DOES NOT EXIST.
- Revenue amounts come from payments.amount.
- Refund amounts come from refunds.amount.
- Join tables using their order_id columns.
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
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("Experiment configuration not found.")

    config = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )

    model = config["model"]
    temperature = config["temperature"]
    question = config["question"]
    conditions = config["conditions"]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_directory = RESULTS / run_id
    run_directory.mkdir(parents=True, exist_ok=True)

    metadata = {
        "run_id": run_id,
        "model": model,
        "temperature": temperature,
        "question": question,
        "conditions": {}
    }

    successful = 0
    failed = 0

    for condition_name, rules in conditions.items():

        print(f"\nGenerating condition: {condition_name}")

        try:
            sql, prompt = generate_sql(
                model,
                temperature,
                question,
                rules
            )

            sql_path = run_directory / f"{condition_name}.sql"

            sql_path.write_text(
                sql,
                encoding="utf-8"
            )

            metadata["conditions"][condition_name] = {
                "status": "generated",
                "sql_file": sql_path.name,
                "prompt_sha256": hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest()
            }

            print("SQL saved locally.")

            # Evaluate this specific generated query.
            evaluation = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "rule_checker.py"),
                    str(sql_path),
                    condition_name
                ],
                cwd=ROOT
            )

            if evaluation.returncode == 0:
                metadata["conditions"][condition_name]["evaluation"] = "completed"
                successful += 1
            else:
                metadata["conditions"][condition_name]["evaluation"] = "failed"
                failed += 1

        except Exception as error:
            metadata["conditions"][condition_name] = {
                "status": "generation_failed",
                "error_type": type(error).__name__
            }

            failed += 1
            print("Condition failed:", type(error).__name__)

    metadata["successful_evaluations"] = successful
    metadata["failed_conditions"] = failed

    metadata_path = run_directory / "metadata.json"

    metadata_path.write_text(
        json.dumps(metadata, indent=4),
        encoding="utf-8"
    )

    print("\nExperiment run finished.")
    print("Run ID:", run_id)
    print("Evaluation records saved locally.")
    print("Metadata saved locally.")


if __name__ == "__main__":
    main()
