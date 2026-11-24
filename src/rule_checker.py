
import sys
import sqlite3
import shutil
import tempfile
import json
import math
import hashlib

from pathlib import Path
from datetime import datetime


ROOT = Path(__file__).resolve().parent.parent

# Backward-compatible default database.
DB_PATH = ROOT / "database" / "research.db"

SQL_PATH = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else ROOT / "results" / "latest_query.sql"
)

LOG_PATH = ROOT / "results" / "rule_test_log.jsonl"


class ResultContractError(Exception):
    pass


def resolve_db_path(db_path=None):
    """Resolve the selected database path."""

    path = Path(db_path) if db_path is not None else DB_PATH
    return path.expanduser().resolve()


def run_query(db_path, sql):
    """Execute SQL against a database in read-only mode."""

    db_path = resolve_db_path(db_path)

    connection = sqlite3.connect(
        db_path.as_uri() + "?mode=ro",
        uri=True
    )

    try:
        connection.execute("PRAGMA query_only = ON")

        cursor = connection.execute(sql)
        rows = cursor.fetchall()

        if len(cursor.description or []) != 1:
            raise ResultContractError(
                "Expected exactly one result column."
            )

        if len(rows) != 1 or len(rows[0]) != 1:
            raise ResultContractError(
                "Expected one row and one column."
            )

        value = rows[0][0]

        if not isinstance(value, (int, float)):
            raise ResultContractError(
                "Expected a numeric result."
            )

        if not math.isfinite(value):
            raise ResultContractError(
                "Result must be finite."
            )

        return float(value)

    finally:
        connection.close()


def create_test_copy(db_path=None):
    """Create an isolated copy for mutation testing."""

    source = resolve_db_path(db_path)

    directory = tempfile.TemporaryDirectory()
    test_db = Path(directory.name) / "test.db"

    try:
        shutil.copy2(source, test_db)
    except Exception:
        directory.cleanup()
        raise

    return directory, test_db


def get_columns(connection, table_name):
    """Return column names for a SQLite table."""

    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {row[1] for row in rows}


def add_customer_order(db_path, verified, deleted):
    """Add a customer, order and payment for mutation testing."""

    connection = sqlite3.connect(db_path)

    try:
        connection.execute("PRAGMA foreign_keys = ON")

        customer_id = connection.execute(
            "SELECT COALESCE(MAX(customer_id), 0) + 1 "
            "FROM customers"
        ).fetchone()[0]

        order_id = connection.execute(
            "SELECT COALESCE(MAX(order_id), 0) + 1 "
            "FROM orders"
        ).fetchone()[0]

        payment_id = connection.execute(
            "SELECT COALESCE(MAX(payment_id), 0) + 1 "
            "FROM payments"
        ).fetchone()[0]

        connection.execute(
            """
            INSERT INTO customers
            (customer_id, name, is_verified, is_deleted)
            VALUES (?, ?, ?, ?)
            """,
            (customer_id, "Test Customer", verified, deleted)
        )

        connection.execute(
            """
            INSERT INTO orders
            (order_id, customer_id, order_date, status)
            VALUES (?, ?, ?, ?)
            """,
            (order_id, customer_id, "2026-02-01", "completed")
        )

        payment_columns = get_columns(connection, "payments")

        if "payment_method_id" in payment_columns:
            method_id = connection.execute(
                """
                SELECT MIN(payment_method_id)
                FROM payment_methods
                """
            ).fetchone()[0]

            connection.execute(
                """
                INSERT INTO payments
                (payment_id, order_id, payment_method_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                (payment_id, order_id, method_id, 1000)
            )
        else:
            connection.execute(
                """
                INSERT INTO payments
                (payment_id, order_id, amount)
                VALUES (?, ?, ?)
                """,
                (payment_id, order_id, 1000)
            )

        connection.commit()

    finally:
        connection.close()

    return order_id


def add_refund(db_path, order_id):
    """Add a refund to the explicitly selected test order."""

    connection = sqlite3.connect(db_path)

    try:
        connection.execute("PRAGMA foreign_keys = ON")

        exists = connection.execute(
            "SELECT 1 FROM orders WHERE order_id = ?",
            (order_id,)
        ).fetchone()

        if exists is None:
            raise ValueError("Refund test order does not exist.")

        refund_id = connection.execute(
            "SELECT COALESCE(MAX(refund_id), 0) + 1 "
            "FROM refunds"
        ).fetchone()[0]

        refund_columns = get_columns(connection, "refunds")

        if "reason_id" in refund_columns:
            reason_id = connection.execute(
                "SELECT MIN(reason_id) FROM refund_reasons"
            ).fetchone()[0]

            connection.execute(
                """
                INSERT INTO refunds
                (refund_id, order_id, reason_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                (refund_id, order_id, reason_id, 100)
            )
        else:
            connection.execute(
                """
                INSERT INTO refunds
                (refund_id, order_id, amount)
                VALUES (?, ?, ?)
                """,
                (refund_id, order_id, 100)
            )

        connection.commit()

    finally:
        connection.close()


def add_multiple_payments_and_refunds(db_path):
    """Add two payments and two refunds to one new order."""

    connection = sqlite3.connect(db_path)

    try:
        connection.execute("PRAGMA foreign_keys = ON")

        customer_id = connection.execute(
            "SELECT COALESCE(MAX(customer_id), 0) + 1 "
            "FROM customers"
        ).fetchone()[0]

        order_id = connection.execute(
            "SELECT COALESCE(MAX(order_id), 0) + 1 "
            "FROM orders"
        ).fetchone()[0]

        payment_id = connection.execute(
            "SELECT COALESCE(MAX(payment_id), 0) + 1 "
            "FROM payments"
        ).fetchone()[0]

        refund_id = connection.execute(
            "SELECT COALESCE(MAX(refund_id), 0) + 1 "
            "FROM refunds"
        ).fetchone()[0]

        connection.execute(
            """
            INSERT INTO customers
            (customer_id, name, is_verified, is_deleted)
            VALUES (?, ?, ?, ?)
            """,
            (customer_id, "Join Test Customer", 1, 0)
        )

        connection.execute(
            """
            INSERT INTO orders
            (order_id, customer_id, order_date, status)
            VALUES (?, ?, ?, ?)
            """,
            (order_id, customer_id, "2026-02-01", "completed")
        )

        payment_columns = get_columns(connection, "payments")

        if "payment_method_id" in payment_columns:
            method_id = connection.execute(
                """
                SELECT MIN(payment_method_id)
                FROM payment_methods
                """
            ).fetchone()[0]

            connection.executemany(
                """
                INSERT INTO payments
                (payment_id, order_id, payment_method_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (payment_id, order_id, method_id, 600),
                    (payment_id + 1, order_id, method_id, 400)
                ]
            )
        else:
            connection.executemany(
                """
                INSERT INTO payments
                (payment_id, order_id, amount)
                VALUES (?, ?, ?)
                """,
                [
                    (payment_id, order_id, 600),
                    (payment_id + 1, order_id, 400)
                ]
            )

        refund_columns = get_columns(connection, "refunds")

        if "reason_id" in refund_columns:
            reason_id = connection.execute(
                """
                SELECT MIN(reason_id)
                FROM refund_reasons
                """
            ).fetchone()[0]

            connection.executemany(
                """
                INSERT INTO refunds
                (refund_id, order_id, reason_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (refund_id, order_id, reason_id, 100),
                    (refund_id + 1, order_id, reason_id, 50)
                ]
            )
        else:
            connection.executemany(
                """
                INSERT INTO refunds
                (refund_id, order_id, amount)
                VALUES (?, ?, ?)
                """,
                [
                    (refund_id, order_id, 100),
                    (refund_id + 1, order_id, 50)
                ]
            )

        connection.commit()

    finally:
        connection.close()


def calculate_reference_total(db_path, condition_name):
    """
    Calculate expected revenue independently using Python.
    """

    valid_conditions = {
        "A_single_deleted_rule",
        "A_single_verified_rule",
        "A_single_join_rule",
        "B_combined_rules",
        "C_reordered_rules"
    }

    if condition_name not in valid_conditions:
        raise ValueError("Unknown experimental condition.")

    exclude_deleted = condition_name in {
        "A_single_deleted_rule",
        "B_combined_rules",
        "C_reordered_rules"
    }

    require_verified = condition_name in {
        "A_single_verified_rule",
        "B_combined_rules",
        "C_reordered_rules"
    }

    # Net revenue is the common metric in every condition.
    subtract_refunds = True

    connection = sqlite3.connect(db_path)

    try:
        orders = connection.execute(
            """
            SELECT
                o.order_id,
                c.is_verified,
                c.is_deleted
            FROM orders o
            JOIN customers c
                ON c.customer_id = o.customer_id
            """
        ).fetchall()

        payments = {}

        for order_id, amount in connection.execute(
            "SELECT order_id, amount FROM payments"
        ):
            payments[order_id] = (
                payments.get(order_id, 0) + amount
            )

        refunds = {}

        for order_id, amount in connection.execute(
            "SELECT order_id, amount FROM refunds"
        ):
            refunds[order_id] = (
                refunds.get(order_id, 0) + amount
            )

        total = 0.0

        for order_id, is_verified, is_deleted in orders:

            if exclude_deleted and is_deleted != 0:
                continue

            if require_verified and is_verified != 1:
                continue

            total += payments.get(order_id, 0)

            if subtract_refunds:
                total -= refunds.get(order_id, 0)

        return float(total)

    finally:
        connection.close()


def evaluate_baseline(sql, condition_name, db_path=None):
    """Compare generated SQL with the independent reference."""

    selected_db = resolve_db_path(db_path)

    actual = run_query(selected_db, sql)

    expected = calculate_reference_total(
        selected_db,
        condition_name
    )

    return {
        "expected": expected,
        "actual": actual,
        "correct": math.isclose(
            actual,
            expected,
            rel_tol=1e-9,
            abs_tol=1e-6
        )
    }


def evaluate_rules(sql, condition_name, db_path=None):
    """Run isolated mutation tests for the selected condition."""

    selected_db = resolve_db_path(db_path)

    # Refund subtraction is a common invariant, not an experimental factor.
    # Each condition is tested for net-revenue behavior plus its assigned rules.
    condition_tests = {
        "A_single_deleted_rule": [
            "refund_rule",
            "deleted_customer_rule"
        ],
        "A_single_verified_rule": [
            "refund_rule",
            "verified_customer_rule"
        ],
        "A_single_join_rule": [
            "refund_rule",
            "join_multiplication_rule"
        ],
        "B_combined_rules": [
            "refund_rule",
            "deleted_customer_rule",
            "verified_customer_rule",
            "join_multiplication_rule"
        ],
        "C_reordered_rules": [
            "refund_rule",
            "deleted_customer_rule",
            "verified_customer_rule",
            "join_multiplication_rule"
        ]
    }

    if condition_name not in condition_tests:
        raise ValueError("Unknown experimental condition.")

    selected_tests = condition_tests[condition_name]

    baseline = run_query(selected_db, sql)
    results = {}

    if "deleted_customer_rule" in selected_tests:
        directory, test_db = create_test_copy(selected_db)

        try:
            add_customer_order(
                test_db,
                verified=1,
                deleted=1
            )

            actual = run_query(test_db, sql)

            results["deleted_customer_rule"] = math.isclose(
                actual,
                baseline,
                abs_tol=1e-6
            )

        finally:
            directory.cleanup()

    if "verified_customer_rule" in selected_tests:
        directory, test_db = create_test_copy(selected_db)

        try:
            add_customer_order(
                test_db,
                verified=0,
                deleted=0
            )

            actual = run_query(test_db, sql)

            results["verified_customer_rule"] = math.isclose(
                actual,
                baseline,
                abs_tol=1e-6
            )

        finally:
            directory.cleanup()

    if "refund_rule" in selected_tests:
        directory, test_db = create_test_copy(selected_db)

        try:
            # Use a fresh eligible order with exactly one payment and no
            # refunds. Compare before/after on the same database copy so
            # existing payment/refund multiplicity cannot confound the delta.
            refund_order_id = add_customer_order(
                test_db,
                verified=1,
                deleted=0
            )
            before_refund = run_query(test_db, sql)

            add_refund(test_db, refund_order_id)

            after_refund = run_query(test_db, sql)

            results["refund_rule"] = math.isclose(
                after_refund - before_refund,
                -100,
                rel_tol=1e-9,
                abs_tol=1e-6
            )

        finally:
            directory.cleanup()

    if "join_multiplication_rule" in selected_tests:
        directory, test_db = create_test_copy(selected_db)

        try:
            add_multiple_payments_and_refunds(test_db)

            actual = run_query(test_db, sql)

            results["join_multiplication_rule"] = math.isclose(
                actual - baseline,
                850,
                abs_tol=1e-6
            )

        finally:
            directory.cleanup()

    return results


def main():

    sql_path = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else SQL_PATH
    )

    condition_name = (
        sys.argv[2] if len(sys.argv) > 2
        else "B_combined_rules"
    )

    run_id = (
        sys.argv[3] if len(sys.argv) > 3
        else None
    )

    trial_number = (
        int(sys.argv[4]) if len(sys.argv) > 4
        else None
    )

    selected_db = (
        resolve_db_path(sys.argv[5])
        if len(sys.argv) > 5
        else DB_PATH.resolve()
    )

    schema_profile = (
        sys.argv[6] if len(sys.argv) > 6
        else "baseline"
    )

    dataset_size = (
        int(sys.argv[7]) if len(sys.argv) > 7
        else None
    )

    record = {
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
        "tests": {},
        "baseline": None,
        "error": None,
        "sql_file": str(sql_path),
        "database": str(selected_db),
        "schema_profile": schema_profile,
        "dataset_size": dataset_size,
        "condition": condition_name,
        "run_id": run_id,
        "trial_number": trial_number
    }

    try:

        if not selected_db.exists():
            raise FileNotFoundError(
                "Selected database not found."
            )

        if not sql_path.exists():
            raise FileNotFoundError(
                "Generated SQL not found."
            )

        if schema_profile not in {"baseline", "expanded"}:
            raise ValueError("Unknown schema profile.")

        sql = sql_path.read_text(
            encoding="utf-8"
        ).strip()

        if not sql.upper().startswith(("SELECT", "WITH")):
            raise ValueError(
                "Only SELECT queries are supported."
            )

        record["query_sha256"] = hashlib.sha256(
            sql.encode("utf-8")
        ).hexdigest()

        # Independent baseline correctness check.
        record["baseline"] = evaluate_baseline(
            sql,
            condition_name,
            selected_db
        )

        # Individual business-rule mutation tests.
        record["tests"] = evaluate_rules(
            sql,
            condition_name,
            selected_db
        )

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

    LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Research evaluation finished.")
    print("Status:", record["status"])

    if record["status"] == "completed":
        print(
            "Baseline correct:",
            record["baseline"]["correct"]
        )
        print("Rule tests:", len(record["tests"]))
        print("Evaluation saved locally.")

    else:
        print("Evaluation error recorded locally.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
