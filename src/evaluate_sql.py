
import sqlite3
import json
import math

from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent

DB_PATH = ROOT / "database" / "research.db"
SQL_PATH = ROOT / "results" / "latest_query.sql"
LOG_PATH = ROOT / "results" / "evaluation_log.jsonl"


def calculate_expected_result(connection):
    """
    Independent reference calculation.
    Uses Python aggregation instead of the model's SQL.
    """

    customers = connection.execute("""
        SELECT customer_id
        FROM customers
        WHERE is_verified = 1
        AND is_deleted = 0
    """).fetchall()

    eligible_customers = {
        customer[0] for customer in customers
    }

    orders = connection.execute("""
        SELECT order_id, customer_id
        FROM orders
    """).fetchall()

    payments = connection.execute("""
        SELECT order_id, amount
        FROM payments
    """).fetchall()

    refunds = connection.execute("""
        SELECT order_id, amount
        FROM refunds
    """).fetchall()

    payment_totals = {}
    refund_totals = {}

    for order_id, amount in payments:
        payment_totals[order_id] = (
            payment_totals.get(order_id, 0) + amount
        )

    for order_id, amount in refunds:
        refund_totals[order_id] = (
            refund_totals.get(order_id, 0) + amount
        )

    expected = 0

    for order_id, customer_id in orders:
        if customer_id not in eligible_customers:
            continue

        expected += payment_totals.get(order_id, 0)
        expected -= refund_totals.get(order_id, 0)

    return expected


def execute_generated_sql(connection, sql):
    """
    Execute model-generated SQL in read-only mode.
    """

    if not sql:
        raise ValueError("Generated SQL is empty.")

    cursor = connection.execute(sql)

    if cursor.description is None:
        raise ValueError("Query did not return a result.")

    rows = cursor.fetchall()

    if len(rows) != 1 or len(rows[0]) != 1:
        raise ValueError(
            "Expected exactly one row and one column."
        )

    value = rows[0][0]

    if value is None or isinstance(value, bool):
        raise ValueError("Query returned an invalid numeric result.")

    if not isinstance(value, (int, float)):
        raise ValueError("Query result is not numeric.")

    if not math.isfinite(value):
        raise ValueError("Query returned a non-finite number.")

    return value


def evaluate():
    record = {
        "timestamp": datetime.now().isoformat(),
        "task": "verified_customer_net_revenue",
        "execution_success": False,
        "correctness": False,
        "error": None
    }

    try:
        if not SQL_PATH.exists():
            raise FileNotFoundError("Generated SQL file not found.")

        sql = SQL_PATH.read_text(encoding="utf-8").strip()

        if not DB_PATH.exists():
            raise FileNotFoundError("Research database not found.")

        connection = sqlite3.connect(
            DB_PATH.as_uri() + "?mode=ro",
            uri=True
        )

        try:
            connection.execute("PRAGMA query_only = ON")

            expected = calculate_expected_result(connection)

            actual = execute_generated_sql(connection, sql)

            record["execution_success"] = True
            record["correctness"] = math.isclose(
                float(actual),
                float(expected),
                rel_tol=1e-9,
                abs_tol=1e-6
            )

            record["expected_result"] = expected
            record["actual_result"] = actual

        finally:
            connection.close()

    except Exception as error:
        record["error"] = str(error)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Evaluation completed.")
    print("Execution successful:", record["execution_success"])
    print("Business correctness:", record["correctness"])

    if record["error"]:
        print("Error:", record["error"])

    print("Evaluation saved locally.")


if __name__ == "__main__":
    evaluate()
