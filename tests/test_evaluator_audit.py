import sqlite3

from database.setup_db import create_schema
from src import rule_checker


CONDITIONS = (
    "A_single_deleted_rule",
    "A_single_verified_rule",
    "A_single_join_rule",
    "B_combined_rules",
    "C_reordered_rules",
)


def make_profile_database(path, profile):
    with sqlite3.connect(path) as connection:
        create_schema(connection, profile)

        connection.executemany(
            """
            INSERT INTO customers
            (customer_id, name, is_verified, is_deleted)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "Eligible", 1, 0),
                (2, "Unverified", 0, 0),
                (3, "Deleted", 1, 1),
            ],
        )
        # Deliberately include different statuses. No experimental condition
        # defines a status filter, so the reference includes all three.
        connection.executemany(
            """
            INSERT INTO orders
            (order_id, customer_id, order_date, status)
            VALUES (?, ?, ?, ?)
            """,
            [
                (101, 1, "2026-01-01", "completed"),
                (102, 2, "2026-01-02", "pending"),
                (103, 3, "2026-01-03", "cancelled"),
            ],
        )

        if profile == "expanded":
            connection.executemany(
                "INSERT INTO payment_methods VALUES (?, ?)",
                [(1, "Card"), (2, "UPI")],
            )
            connection.executemany(
                "INSERT INTO refund_reasons VALUES (?, ?)",
                [(1, "Return"), (2, "Other")],
            )
            connection.executemany(
                """
                INSERT INTO payments
                (payment_id, order_id, payment_method_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                [(1, 101, 1, 100), (2, 102, 2, 200), (3, 103, 1, 300)],
            )
            connection.execute(
                """
                INSERT INTO refunds
                (refund_id, order_id, reason_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                (1, 101, 1, 10),
            )
        else:
            connection.executemany(
                "INSERT INTO payments VALUES (?, ?, ?)",
                [(1, 101, 100), (2, 102, 200), (3, 103, 300)],
            )
            connection.execute(
                "INSERT INTO refunds VALUES (?, ?, ?)",
                (1, 101, 10),
            )


def correct_sql(condition):
    filters = {
        "A_single_deleted_rule": "WHERE c.is_deleted = 0",
        "A_single_verified_rule": "WHERE c.is_verified = 1",
        "A_single_join_rule": "",
        "B_combined_rules": "WHERE c.is_deleted = 0 AND c.is_verified = 1",
        "C_reordered_rules": "WHERE c.is_verified = 1 AND c.is_deleted = 0",
    }
    return f"""
        WITH eligible_orders AS (
            SELECT o.order_id
            FROM orders AS o
            JOIN customers AS c
              ON c.customer_id = o.customer_id
            {filters[condition]}
        )
        SELECT
            COALESCE((
                SELECT SUM(p.amount)
                FROM payments AS p
                JOIN eligible_orders AS e ON e.order_id = p.order_id
            ), 0)
            -
            COALESCE((
                SELECT SUM(r.amount)
                FROM refunds AS r
                JOIN eligible_orders AS e ON e.order_id = r.order_id
            ), 0)
    """


def independent_sql_reference(condition):
    filters = {
        "A_single_deleted_rule": "WHERE is_deleted = 0",
        "A_single_verified_rule": "WHERE is_verified = 1",
        "A_single_join_rule": "",
        "B_combined_rules": "WHERE is_deleted = 0 AND is_verified = 1",
        "C_reordered_rules": "WHERE is_verified = 1 AND is_deleted = 0",
    }
    return f"""
        WITH payment_totals AS (
            SELECT order_id, SUM(amount) AS total
            FROM payments
            GROUP BY order_id
        ),
        refund_totals AS (
            SELECT order_id, SUM(amount) AS total
            FROM refunds
            GROUP BY order_id
        ),
        per_order AS (
            SELECT
                o.order_id,
                c.is_verified,
                c.is_deleted,
                COALESCE(p.total, 0) - COALESCE(r.total, 0) AS net_revenue
            FROM orders AS o
            JOIN customers AS c ON c.customer_id = o.customer_id
            LEFT JOIN payment_totals AS p ON p.order_id = o.order_id
            LEFT JOIN refund_totals AS r ON r.order_id = o.order_id
        )
        SELECT COALESCE(SUM(net_revenue), 0)
        FROM per_order
        {filters[condition]}
    """


def table_counts(path):
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("customers", "orders", "payments", "refunds")
        }


def test_python_reference_matches_independent_sql_oracle_on_both_schemas(tmp_path):
    expected = {
        "A_single_deleted_rule": 290.0,
        "A_single_verified_rule": 390.0,
        "A_single_join_rule": 590.0,
        "B_combined_rules": 90.0,
        "C_reordered_rules": 90.0,
    }

    for profile in ("baseline", "expanded"):
        db_path = tmp_path / f"{profile}.db"
        make_profile_database(db_path, profile)

        for condition in CONDITIONS:
            python_total = rule_checker.calculate_reference_total(
                db_path, condition
            )
            sql_total = rule_checker.run_query(
                db_path, independent_sql_reference(condition)
            )
            assert python_total == expected[condition]
            assert sql_total == expected[condition]
            assert python_total == sql_total


def test_known_correct_sql_passes_all_evaluator_checks_on_both_schemas(tmp_path):
    for profile in ("baseline", "expanded"):
        db_path = tmp_path / f"{profile}_evaluator.db"
        make_profile_database(db_path, profile)
        original_counts = table_counts(db_path)

        for condition in CONDITIONS:
            sql = correct_sql(condition)
            baseline = rule_checker.evaluate_baseline(
                sql, condition, db_path
            )
            assert baseline["correct"] is True, (profile, condition)

            tests = rule_checker.evaluate_rules(sql, condition, db_path)
            assert tests, (profile, condition)
            assert all(value is True for value in tests.values()), (
                profile,
                condition,
                tests,
            )

        # Mutation tests operate only on temporary copies.
        assert table_counts(db_path) == original_counts


def test_reference_includes_all_statuses_when_no_status_rule_is_supplied(tmp_path):
    db_path = tmp_path / "statuses.db"
    make_profile_database(db_path, "baseline")

    # For the join-only condition, all three customers' orders count:
    # completed (90 net), pending (200), and cancelled (300).
    assert rule_checker.calculate_reference_total(
        db_path, "A_single_join_rule"
    ) == 590.0
