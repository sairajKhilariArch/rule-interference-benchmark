
import json
from datetime import datetime
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8645/v1",
    api_key="test"
)

MODEL = "upstage/solar-mini4:free"

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "results"

SCHEMA = """
Tables:

customers(
    customer_id,
    name,
    is_verified,
    is_deleted
)

orders(
    order_id,
    customer_id,
    order_date,
    status
)

order_items(
    item_id,
    order_id,
    product_name,
    quantity,
    unit_price
)

payments(
    payment_id,
    order_id,
    amount
)

refunds(
    refund_id,
    order_id,
    amount
)
"""


def generate_sql(question, rules):
    prompt = f"""
You are an SQL generator.

Database schema:
{SCHEMA}

Business rules:
{rules}

Question:
{question}

Return only one SQLite SELECT query.
Do not explain your answer.
Do not use Markdown code blocks.
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    return response.choices[0].message.content.strip()


if __name__ == "__main__":

    rules = """
    1. Exclude deleted customers.
    2. Include only verified customers.
    3. Subtract refunds from revenue.
    """

    question = "What is the net revenue from verified customers?"

    sql = generate_sql(question, rules)

    print("\nGenerated SQL:\n")
    print(sql)

    RESULTS.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Preserve original generated SQL
    query_file = RESULTS / f"query_{timestamp}.sql"
    query_file.write_text(sql, encoding="utf-8")

    # Save latest query
    latest_file = RESULTS / "latest_query.sql"
    latest_file.write_text(sql, encoding="utf-8")

    # Save generation metadata
    metadata = {
        "timestamp": timestamp,
        "model": MODEL,
        "temperature": 0
    }

    metadata_file = RESULTS / "generation_metadata.jsonl"

    with metadata_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(metadata) + "\n")

    print("\nOriginal SQL preserved locally.")
    print("Generation metadata saved locally.")
