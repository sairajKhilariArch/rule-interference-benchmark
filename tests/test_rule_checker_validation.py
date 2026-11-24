import json
import sys
import sqlite3

import pytest

from src import rule_checker


CORRECT_SQL = """
WITH payment_totals AS (
    SELECT order_id, SUM(amount) AS total
    FROM payments
    GROUP BY order_id
),
refund_totals AS (
    SELECT order_id, SUM(amount) AS total
    FROM refunds
    GROUP BY order_id
)
SELECT COALESCE(
    SUM(COALESCE(p.total, 0) - COALESCE(r.total, 0)),
    0
)
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payment_totals p ON p.order_id = o.order_id
LEFT JOIN refund_totals r ON r.order_id = o.order_id
WHERE c.is_verified = 1
  AND c.is_deleted = 0;
"""


# Deliberately incorrect: does not exclude deleted customers.
BAD_DELETED_SQL = """
SELECT COALESCE(SUM(p.amount), 0) - COALESCE(SUM(r.amount), 0)
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payments p ON p.order_id = o.order_id
LEFT JOIN refunds r ON r.order_id = o.order_id
WHERE c.is_verified = 1;
"""


# Deliberately incorrect: does not exclude unverified customers.
BAD_VERIFIED_SQL = """
SELECT COALESCE(SUM(p.amount), 0) - COALESCE(SUM(r.amount), 0)
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payments p ON p.order_id = o.order_id
LEFT JOIN refunds r ON r.order_id = o.order_id
WHERE c.is_deleted = 0;
"""


# Deliberately incorrect: ignores refunds.
BAD_REFUND_SQL = """
SELECT COALESCE(SUM(p.amount), 0)
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payments p ON p.order_id = o.order_id
WHERE c.is_verified = 1
  AND c.is_deleted = 0;
"""


# Deliberately incorrect: joins two one-to-many tables before aggregating.
BAD_JOIN_SQL = """
SELECT COALESCE(SUM(p.amount), 0) - COALESCE(SUM(r.amount), 0)
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payments p ON p.order_id = o.order_id
LEFT JOIN refunds r ON r.order_id = o.order_id
WHERE c.is_verified = 1
  AND c.is_deleted = 0;
"""


@pytest.fixture
def synthetic_database(tmp_path, monkeypatch):
    """Create an isolated database and point the evaluator at it."""

    db_path = tmp_path / "synthetic_research.db"

    connection = sqlite3.connect(db_path)

    connection.executescript("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            is_verified INTEGER NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE payments (
            payment_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL
        );

        CREATE TABLE refunds (
            refund_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL
        );

        INSERT INTO customers VALUES
            (1, 'Eligible Customer', 1, 0),
            (2, 'Unverified Customer', 0, 0),
            (3, 'Deleted Customer', 1, 1);

        INSERT INTO orders VALUES
            (101, 1, '2026-01-01', 'completed'),
            (102, 2, '2026-01-02', 'completed'),
            (103, 3, '2026-01-03', 'completed');

        INSERT INTO payments VALUES
            (1, 101, 100),
            (2, 102, 200),
            (3, 103, 300);

        INSERT INTO refunds VALUES
            (1, 101, 10);
    """)

    connection.commit()
    connection.close()

    monkeypatch.setattr(rule_checker, "DB_PATH", db_path)

    return db_path


def test_correct_query_passes_all_four_rules(synthetic_database):
    results = rule_checker.evaluate_rules(
        CORRECT_SQL,
        "B_combined_rules"
    )

    assert set(results) == {
        "deleted_customer_rule",
        "verified_customer_rule",
        "refund_rule",
        "join_multiplication_rule"
    }

    assert all(value is True for value in results.values())


def test_correct_query_passes_reordered_condition(synthetic_database):
    results = rule_checker.evaluate_rules(
        CORRECT_SQL,
        "C_reordered_rules"
    )

    assert len(results) == 4
    assert all(value is True for value in results.values())


def test_deleted_customer_violation_is_detected(synthetic_database):
    results = rule_checker.evaluate_rules(
        BAD_DELETED_SQL,
        "A_single_deleted_rule"
    )

    assert results["deleted_customer_rule"] is False


def test_verified_customer_violation_is_detected(synthetic_database):
    results = rule_checker.evaluate_rules(
        BAD_VERIFIED_SQL,
        "A_single_verified_rule"
    )

    assert results["verified_customer_rule"] is False


def test_refund_violation_is_detected(synthetic_database):
    results = rule_checker.evaluate_rules(
        BAD_REFUND_SQL,
        "A_single_refund_rule"
    )

    assert set(results) == {"refund_rule"}
    assert results["refund_rule"] is False


def test_join_multiplication_violation_is_detected(synthetic_database):
    results = rule_checker.evaluate_rules(
        BAD_JOIN_SQL,
        "A_single_join_rule"
    )

    assert set(results) == {"join_multiplication_rule"}
    assert results["join_multiplication_rule"] is False


@pytest.mark.parametrize(
    "condition",
    ["B_combined_rules", "C_reordered_rules"]
)
def test_combined_conditions_detect_each_rule_violation(
    synthetic_database,
    condition
):
    incorrect_queries = [
        ("deleted_customer_rule", BAD_DELETED_SQL),
        ("verified_customer_rule", BAD_VERIFIED_SQL),
        ("refund_rule", BAD_REFUND_SQL),
        ("join_multiplication_rule", BAD_JOIN_SQL),
    ]

    for expected_failure, sql in incorrect_queries:
        results = rule_checker.evaluate_rules(sql, condition)

        assert results[expected_failure] is False, (
            f"{condition} failed to detect {expected_failure}"
        )


def test_unknown_condition_is_rejected(synthetic_database):
    with pytest.raises(ValueError):
        rule_checker.evaluate_rules(CORRECT_SQL, "unknown_condition")


def test_valid_scalar_query_returns_number(synthetic_database):
    result = rule_checker.run_query(
        synthetic_database,
        "SELECT 42"
    )

    assert result == 42.0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT 1, 2",
        "SELECT 'not a number'",
        "SELECT NULL",
        "SELECT 1e999",
    ]
)
def test_invalid_result_contract_is_rejected(
    synthetic_database,
    sql
):
    with pytest.raises(rule_checker.ResultContractError):
        rule_checker.run_query(synthetic_database, sql)


def test_write_query_is_rejected(synthetic_database):
    with pytest.raises(sqlite3.Error):
        rule_checker.run_query(
            synthetic_database,
            "INSERT INTO customers VALUES (99, 'Unsafe', 1, 0)"
        )

    # Confirm that the temporary database was not modified.
    connection = sqlite3.connect(synthetic_database)

    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM customers WHERE customer_id = 99"
        ).fetchone()[0]

        assert count == 0
    finally:
        connection.close()


def run_main_and_read_record(
    monkeypatch,
    tmp_path,
    synthetic_database,
    sql,
    condition="B_combined_rules"
):
    sql_file = tmp_path / "generated.sql"
    log_file = tmp_path / "test_log.jsonl"

    sql_file.write_text(sql, encoding="utf-8")

    monkeypatch.setattr(rule_checker, "DB_PATH", synthetic_database)
    monkeypatch.setattr(rule_checker, "SQL_PATH", sql_file)
    monkeypatch.setattr(rule_checker, "LOG_PATH", log_file)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rule_checker.py",
            str(sql_file),
            condition,
            "validation-run",
            "1"
        ]
    )

    exit_code = 0

    try:
        rule_checker.main()
    except SystemExit as error:
        exit_code = error.code

    record = json.loads(log_file.read_text(encoding="utf-8").strip())

    return record, exit_code


def test_syntax_error_is_sql_execution_failure(
    monkeypatch, tmp_path, synthetic_database
):
    record, exit_code = run_main_and_read_record(
        monkeypatch,
        tmp_path,
        synthetic_database,
        "SELECT FROM"
    )

    assert record["status"] == "sql_execution_failure"
    assert exit_code == 1


def test_wrong_result_shape_is_result_contract_failure(
    monkeypatch, tmp_path, synthetic_database
):
    record, exit_code = run_main_and_read_record(
        monkeypatch,
        tmp_path,
        synthetic_database,
        "SELECT 1, 2"
    )

    assert record["status"] == "result_contract_failure"
    assert exit_code == 1


def test_business_rule_violation_is_not_execution_failure(
    monkeypatch, tmp_path, synthetic_database
):
    record, exit_code = run_main_and_read_record(
        monkeypatch,
        tmp_path,
        synthetic_database,
        BAD_DELETED_SQL,
        "A_single_deleted_rule"
    )

    assert record["status"] == "completed"
    assert record["tests"]["deleted_customer_rule"] is False
    assert exit_code == 0


def test_unknown_condition_is_evaluator_failure(
    monkeypatch, tmp_path, synthetic_database
):
    record, exit_code = run_main_and_read_record(
        monkeypatch,
        tmp_path,
        synthetic_database,
        CORRECT_SQL,
        "unknown_condition"
    )

    assert record["status"] == "evaluation_failure"
    assert exit_code == 1


def independent_reference_total(db_path):
    """
    Independent Python calculation for the combined B/C condition.
    Does not reuse the generated SQL.
    """
    connection = sqlite3.connect(db_path)

    try:
        eligible_customers = {
            row[0]
            for row in connection.execute("""
                SELECT customer_id
                FROM customers
                WHERE is_verified = 1
                  AND is_deleted = 0
            """)
        }

        orders = connection.execute("""
            SELECT order_id, customer_id
            FROM orders
        """).fetchall()

        payments = {}
        for order_id, amount in connection.execute("""
            SELECT order_id, amount FROM payments
        """):
            payments[order_id] = payments.get(order_id, 0) + amount

        refunds = {}
        for order_id, amount in connection.execute("""
            SELECT order_id, amount FROM refunds
        """):
            refunds[order_id] = refunds.get(order_id, 0) + amount

        total = 0

        for order_id, customer_id in orders:
            if customer_id in eligible_customers:
                total += payments.get(order_id, 0)
                total -= refunds.get(order_id, 0)

        return float(total)

    finally:
        connection.close()


def test_correct_query_matches_independent_reference(synthetic_database):
    actual = rule_checker.run_query(
        synthetic_database,
        CORRECT_SQL
    )

    expected = independent_reference_total(synthetic_database)

    assert actual == expected


def test_metamorphic_checks_can_miss_constant_offset(synthetic_database):
    # This query is deliberately wrong: it adds 123 to the correct result.
    offset_sql = f"""
        SELECT ({CORRECT_SQL.strip().rstrip(';')}) + 123
    """

    # Existing metamorphic checks only observe changes.
    results = rule_checker.evaluate_rules(
        offset_sql,
        "B_combined_rules"
    )

    # The current evaluator may accept every individual change test.
    assert all(value is True for value in results.values())

    # But its original answer is wrong.
    actual = rule_checker.run_query(
        synthetic_database,
        offset_sql
    )

    expected = independent_reference_total(synthetic_database)

    assert actual != expected
import json
import sys
import sqlite3
import pytest

from src import rule_checker

@pytest.fixture
def expanded_database(tmp_path):
    """Create an isolated expanded-schema database."""

    from database.setup_db import create_schema

    db_path = tmp_path / "expanded_research.db"

    with sqlite3.connect(db_path) as connection:
        create_schema(connection, "expanded")

        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            [
                (1, "Eligible Customer", 1, 0),
                (2, "Unverified Customer", 0, 0),
                (3, "Deleted Customer", 1, 1)
            ]
        )

        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?)",
            [
                (101, 1, "2026-01-01", "completed"),
                (102, 2, "2026-01-02", "completed"),
                (103, 3, "2026-01-03", "completed")
            ]
        )

        connection.executemany(
            "INSERT INTO categories VALUES (?, ?)",
            [(1, "Electronics")]
        )

        connection.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?)",
            [(1, "Laptop", 1, 50000.0)]
        )

        connection.executemany(
            "INSERT INTO order_items VALUES (?, ?, ?, ?)",
            [
                (1, 101, 1, 1),
                (2, 102, 1, 1),
                (3, 103, 1, 1)
            ]
        )

        connection.executemany(
            "INSERT INTO payment_methods VALUES (?, ?)",
            [(1, "UPI")]
        )

        connection.executemany(
            "INSERT INTO payments VALUES (?, ?, ?, ?)",
            [
                (1, 101, 1, 100),
                (2, 102, 1, 200),
                (3, 103, 1, 300)
            ]
        )

        connection.executemany(
            "INSERT INTO refund_reasons VALUES (?, ?)",
            [(1, "Customer Return")]
        )

        connection.executemany(
            "INSERT INTO refunds VALUES (?, ?, ?, ?)",
            [(1, 101, 1, 10)]
        )

    return db_path


def test_expanded_schema_reference_calculation(expanded_database):
    result = rule_checker.calculate_reference_total(
        expanded_database,
        "B_combined_rules"
    )

    assert result == 90.0


def test_expanded_schema_correct_query_passes_all_rules(
    expanded_database
):
    expected = rule_checker.evaluate_baseline(
        CORRECT_SQL,
        "B_combined_rules",
        expanded_database
    )

    results = rule_checker.evaluate_rules(
        CORRECT_SQL,
        "B_combined_rules",
        expanded_database
    )

    assert expected["correct"] is True
    assert set(results) == {
        "deleted_customer_rule",
        "verified_customer_rule",
        "refund_rule",
        "join_multiplication_rule"
    }
    assert all(value is True for value in results.values())


def test_expanded_schema_detects_deleted_customer_violation(
    expanded_database
):
    results = rule_checker.evaluate_rules(
        BAD_DELETED_SQL,
        "A_single_deleted_rule",
        expanded_database
    )

    assert results["deleted_customer_rule"] is False


def test_expanded_schema_detects_verified_customer_violation(
    expanded_database
):
    results = rule_checker.evaluate_rules(
        BAD_VERIFIED_SQL,
        "A_single_verified_rule",
        expanded_database
    )

    assert results["verified_customer_rule"] is False


def test_expanded_schema_detects_refund_violation(
    expanded_database
):
    results = rule_checker.evaluate_rules(
        BAD_REFUND_SQL,
        "A_single_refund_rule",
        expanded_database
    )

    assert results["refund_rule"] is False


def test_expanded_schema_detects_join_multiplication(
    expanded_database
):
    results = rule_checker.evaluate_rules(
        BAD_JOIN_SQL,
        "A_single_join_rule",
        expanded_database
    )

    assert results["join_multiplication_rule"] is False
