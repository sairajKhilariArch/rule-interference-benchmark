import json
from pathlib import Path
from types import SimpleNamespace

from src import experiment_runner as runner
from src import rule_checker
from src import reevaluate_run


ROOT = Path(__file__).resolve().parent.parent


def make_database(path):
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                is_verified INTEGER NOT NULL,
                is_deleted INTEGER NOT NULL
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
                (1, 'Eligible', 1, 0),
                (2, 'Unverified', 0, 0),
                (3, 'Deleted', 1, 1);
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


def test_configuration_has_five_conditions_and_three_experimental_rules():
    config = json.loads(
        (ROOT / "experiments" / "conditions.json").read_text(encoding="utf-8")
    )
    conditions = {item["name"]: item["rules"] for item in config["conditions"]}

    assert set(conditions) == {
        "A_single_deleted_rule",
        "A_single_verified_rule",
        "A_single_join_rule",
        "B_combined_rules",
        "C_reordered_rules",
    }
    assert len(config["conditions"]) == 5
    assert conditions["C_reordered_rules"] == list(
        reversed(conditions["B_combined_rules"])
    )
    assert len(conditions["B_combined_rules"]) == 3
    assert config["trials_per_condition"] * len(config["dataset_sizes"]) * len(
        config["schema_profiles"]
    ) * len(config["conditions"]) == 300


def test_reference_calculator_uses_net_revenue_for_every_condition(tmp_path):
    db_path = tmp_path / "net_revenue.db"
    make_database(db_path)

    expected = {
        "A_single_deleted_rule": 290.0,
        "A_single_verified_rule": 390.0,
        "A_single_join_rule": 590.0,
        "B_combined_rules": 90.0,
        "C_reordered_rules": 90.0,
    }

    for condition, total in expected.items():
        assert rule_checker.calculate_reference_total(
            db_path, condition
        ) == total


def test_generation_prompt_declares_common_net_revenue_without_forcing_join_rule(
    monkeypatch,
):
    captured = {}

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="SELECT 1")
                    )
                ]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setattr(runner, "client", fake_client)

    sql, prompt = runner.generate_sql(
        "test-model",
        0,
        "Calculate net revenue.",
        ["Exclude orders belonging to deleted customers."],
        "baseline",
    )

    assert sql == "SELECT 1"
    assert "SUM(payments.amount) - SUM(refunds.amount)" in prompt
    assert "Do not assume customer eligibility filters" in prompt
    assert "Aggregate payments and refunds separately per order" not in prompt
    assert captured["messages"][0]["content"] == prompt



def correct_sql_for_condition(condition_name):
    """Build a known-correct query for evaluator integration tests."""

    filters = {
        "A_single_deleted_rule": "WHERE c.is_deleted = 0",
        "A_single_verified_rule": "WHERE c.is_verified = 1",
        "A_single_join_rule": "",
        "B_combined_rules": (
            "WHERE c.is_deleted = 0 AND c.is_verified = 1"
        ),
        "C_reordered_rules": (
            "WHERE c.is_verified = 1 AND c.is_deleted = 0"
        ),
    }

    return f"""
        WITH eligible_orders AS (
            SELECT o.order_id
            FROM orders AS o
            JOIN customers AS c
              ON c.customer_id = o.customer_id
            {filters[condition_name]}
        )
        SELECT
            COALESCE((
                SELECT SUM(p.amount)
                FROM payments AS p
                JOIN eligible_orders AS e
                  ON e.order_id = p.order_id
            ), 0)
            -
            COALESCE((
                SELECT SUM(r.amount)
                FROM refunds AS r
                JOIN eligible_orders AS e
                  ON e.order_id = r.order_id
            ), 0)
    """


def test_production_evaluator_accepts_known_correct_sql_for_all_conditions(
    tmp_path,
):
    db_path = tmp_path / "evaluator_integration.db"
    make_database(db_path)

    conditions = [
        "A_single_deleted_rule",
        "A_single_verified_rule",
        "A_single_join_rule",
        "B_combined_rules",
        "C_reordered_rules",
    ]

    for condition in conditions:
        sql = correct_sql_for_condition(condition)

        baseline = rule_checker.evaluate_baseline(
            sql,
            condition,
            db_path,
        )
        assert baseline["correct"] is True, condition

        mutation_results = rule_checker.evaluate_rules(
            sql,
            condition,
            db_path,
        )
        assert mutation_results, condition
        assert all(value is True for value in mutation_results.values()), (
            condition,
            mutation_results,
        )


def test_refund_mutation_does_not_change_original_database(tmp_path):
    import sqlite3

    db_path = tmp_path / "refund_isolation.db"
    make_database(db_path)

    with sqlite3.connect(db_path) as connection:
        before = {
            "customers": connection.execute(
                "SELECT COUNT(*) FROM customers"
            ).fetchone()[0],
            "orders": connection.execute(
                "SELECT COUNT(*) FROM orders"
            ).fetchone()[0],
            "payments": connection.execute(
                "SELECT COUNT(*) FROM payments"
            ).fetchone()[0],
            "refunds": connection.execute(
                "SELECT COUNT(*) FROM refunds"
            ).fetchone()[0],
        }

    sql = correct_sql_for_condition("A_single_join_rule")
    results = rule_checker.evaluate_rules(
        sql,
        "A_single_join_rule",
        db_path,
    )
    assert results["refund_rule"] is True

    with sqlite3.connect(db_path) as connection:
        after = {
            "customers": connection.execute(
                "SELECT COUNT(*) FROM customers"
            ).fetchone()[0],
            "orders": connection.execute(
                "SELECT COUNT(*) FROM orders"
            ).fetchone()[0],
            "payments": connection.execute(
                "SELECT COUNT(*) FROM payments"
            ).fetchone()[0],
            "refunds": connection.execute(
                "SELECT COUNT(*) FROM refunds"
            ).fetchone()[0],
        }

    assert after == before



def test_refund_mutation_is_independent_of_existing_join_fanout(tmp_path):
    import sqlite3

    db_path = tmp_path / "refund_with_existing_fanout.db"
    make_database(db_path)

    # Make the first eligible order multi-payment and multi-refund. The
    # previous mutation selected this arbitrary order, coupling the refund
    # result to payment/refund join fan-out.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO payments VALUES (4, 101, 50)"
        )
        connection.execute(
            "INSERT INTO refunds VALUES (2, 101, 5)"
        )

    naive_join_sql = """
        SELECT COALESCE(SUM(p.amount), 0)
             - COALESCE(SUM(r.amount), 0)
        FROM orders AS o
        JOIN customers AS c
          ON c.customer_id = o.customer_id
        LEFT JOIN payments AS p
          ON p.order_id = o.order_id
        LEFT JOIN refunds AS r
          ON r.order_id = o.order_id
    """

    results = rule_checker.evaluate_rules(
        naive_join_sql,
        "A_single_join_rule",
        db_path,
    )

    # The refund check is now isolated and should pass even though this SQL
    # still fails the separate multi-payment/multi-refund fan-out test.
    assert results["refund_rule"] is True
    assert results["join_multiplication_rule"] is False



def test_reevaluation_rejects_sql_paths_outside_run_directory(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    allowed = reevaluate_run.safe_child(run_dir, "saved.sql")
    assert allowed == (run_dir / "saved.sql").resolve()

    try:
        reevaluate_run.safe_child(run_dir, "../outside.sql")
    except ValueError:
        pass
    else:
        raise AssertionError("Path traversal should be rejected.")
