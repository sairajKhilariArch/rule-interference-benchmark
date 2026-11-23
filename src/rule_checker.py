
import sys
import sqlite3
import shutil
import tempfile
import json
import math
import hashlib

from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "research.db"
SQL_PATH = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else ROOT / "results" / "latest_query.sql"
)
LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"


class ResultContractError(Exception):
    pass


def run_query(db_path, sql):
    connection = sqlite3.connect(
        db_path.as_uri() + "?mode=ro",
        uri=True
    )

    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(sql)
        rows = cursor.fetchall()

        if len(rows) != 1 or len(rows[0]) != 1:
            raise ResultContractError("Expected one row and one column.")

        value = rows[0][0]

        if not isinstance(value, (int, float)):
            raise ResultContractError("Expected a numeric result.")

        if not math.isfinite(value):
            raise ResultContractError("Result must be finite.")

        return float(value)

    finally:
        connection.close()


def create_test_copy():
    directory = tempfile.TemporaryDirectory()
    test_db = Path(directory.name) / "test.db"
    shutil.copy2(DB_PATH, test_db)
    return directory, test_db


def add_customer_order(db_path, verified, deleted):
    connection = sqlite3.connect(db_path)

    try:
        customer_id = connection.execute(
            "SELECT COALESCE(MAX(customer_id), 0) + 1 FROM customers"
        ).fetchone()[0]

        order_id = connection.execute(
            "SELECT COALESCE(MAX(order_id), 0) + 1 FROM orders"
        ).fetchone()[0]

        payment_id = connection.execute(
            "SELECT COALESCE(MAX(payment_id), 0) + 1 FROM payments"
        ).fetchone()[0]

        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            (customer_id, "Test Customer", verified, deleted)
        )

        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?)",
            (order_id, customer_id, "2026-02-01", "completed")
        )

        connection.execute(
            "INSERT INTO payments VALUES (?, ?, ?)",
            (payment_id, order_id, 1000)
        )

        connection.commit()

    finally:
        connection.close()


def add_refund(db_path):
    connection = sqlite3.connect(db_path)

    try:
        order = connection.execute("""
            SELECT o.order_id
            FROM orders o
            JOIN customers c ON c.customer_id = o.customer_id
            WHERE c.is_verified = 1
              AND c.is_deleted = 0
            LIMIT 1
        """).fetchone()

        if order is None:
            raise ValueError("No eligible customer order found.")

        refund_id = connection.execute(
            "SELECT COALESCE(MAX(refund_id), 0) + 1 FROM refunds"
        ).fetchone()[0]

        connection.execute(
            "INSERT INTO refunds VALUES (?, ?, ?)",
            (refund_id, order[0], 100)
        )

        connection.commit()

    finally:
        connection.close()


def add_multiple_payments_and_refunds(db_path):
    connection = sqlite3.connect(db_path)

    try:
        customer_id = connection.execute(
            "SELECT COALESCE(MAX(customer_id), 0) + 1 FROM customers"
        ).fetchone()[0]

        order_id = connection.execute(
            "SELECT COALESCE(MAX(order_id), 0) + 1 FROM orders"
        ).fetchone()[0]

        payment_id = connection.execute(
            "SELECT COALESCE(MAX(payment_id), 0) + 1 FROM payments"
        ).fetchone()[0]

        refund_id = connection.execute(
            "SELECT COALESCE(MAX(refund_id), 0) + 1 FROM refunds"
        ).fetchone()[0]

        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            (customer_id, "Join Test Customer", 1, 0)
        )

        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?)",
            (order_id, customer_id, "2026-02-01", "completed")
        )

        connection.executemany(
            "INSERT INTO payments VALUES (?, ?, ?)",
            [
                (payment_id, order_id, 600),
                (payment_id + 1, order_id, 400)
            ]
        )

        connection.executemany(
            "INSERT INTO refunds VALUES (?, ?, ?)",
            [
                (refund_id, order_id, 100),
                (refund_id + 1, order_id, 50)
            ]
        )

        connection.commit()

    finally:
        connection.close()



def evaluate_rules(sql, condition_name):

    condition_tests = {
        "A_single_deleted_rule": [
            "deleted_customer_rule"
        ],
        "A_single_verified_rule": [
            "verified_customer_rule"
        ],
        "A_single_refund_rule": [
            "refund_rule"
        ],
        "A_single_join_rule": [
            "join_multiplication_rule"
        ],
        "B_combined_rules": [
            "deleted_customer_rule",
            "verified_customer_rule",
            "refund_rule",
            "join_multiplication_rule"
        ],
        "C_reordered_rules": [
            "deleted_customer_rule",
            "verified_customer_rule",
            "refund_rule",
            "join_multiplication_rule"
        ]
    }

    if condition_name not in condition_tests:
        raise ValueError("Unknown experimental condition.")

    selected_tests = condition_tests[condition_name]
    baseline = run_query(DB_PATH, sql)
    results = {}

    if "deleted_customer_rule" in selected_tests:
        directory, test_db = create_test_copy()
        try:
            add_customer_order(test_db, verified=1, deleted=1)
            actual = run_query(test_db, sql)
            results["deleted_customer_rule"] = math.isclose(
                actual, baseline, abs_tol=1e-6
            )
        finally:
            directory.cleanup()

    if "verified_customer_rule" in selected_tests:
        directory, test_db = create_test_copy()
        try:
            add_customer_order(test_db, verified=0, deleted=0)
            actual = run_query(test_db, sql)
            results["verified_customer_rule"] = math.isclose(
                actual, baseline, abs_tol=1e-6
            )
        finally:
            directory.cleanup()

    if "refund_rule" in selected_tests:
        directory, test_db = create_test_copy()
        try:
            add_refund(test_db)
            actual = run_query(test_db, sql)
            results["refund_rule"] = math.isclose(
                actual - baseline, -100, abs_tol=1e-6
            )
        finally:
            directory.cleanup()

    if "join_multiplication_rule" in selected_tests:
        directory, test_db = create_test_copy()
        try:
            add_multiple_payments_and_refunds(test_db)
            actual = run_query(test_db, sql)
            results["join_multiplication_rule"] = math.isclose(
                actual - baseline, 850, abs_tol=1e-6
            )
        finally:
            directory.cleanup()

    return results


def main():
    record = {
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
        "tests": {},
        "error": None,
        "sql_file": str(SQL_PATH)
    }

    try:
        if not DB_PATH.exists():
            raise FileNotFoundError("Database not found.")

        if not SQL_PATH.exists():
            raise FileNotFoundError("Generated SQL not found.")

        sql = SQL_PATH.read_text(encoding="utf-8").strip()

        if not sql.upper().startswith(("SELECT", "WITH")):
            raise ValueError("Only SELECT queries are supported.")

        record["query_sha256"] = hashlib.sha256(
            sql.encode("utf-8")
        ).hexdigest()

        condition_name = (
            sys.argv[2] if len(sys.argv) > 2
            else "B_combined_rules"
        )

        record["condition"] = condition_name
        record["run_id"] = (
            sys.argv[3] if len(sys.argv) > 3 else None
        )
        record["trial_number"] = (
            int(sys.argv[4]) if len(sys.argv) > 4 else None
        )
        record["tests"] = evaluate_rules(sql, condition_name)
        record["status"] = "completed"

    except sqlite3.Error as error:
        record["status"] = "sql_execution_failure"
        record["error"] = str(error)

    except ResultContractError as error:
        record["status"] = "result_contract_failure"
        record["error"] = str(error)

    except Exception as error:
        record["status"] = "evaluation_failure"
        record["error"] = str(error)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Research evaluation finished.")
    print("Status:", record["status"])

    if record["status"] == "completed":
        print("Rule tests:", len(record["tests"]))
        print("Evaluation saved locally.")
    else:
        print("Evaluation error recorded locally.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()