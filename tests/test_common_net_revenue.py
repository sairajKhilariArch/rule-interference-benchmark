import json
from pathlib import Path
from types import SimpleNamespace

from src import experiment_runner as runner
from src import rule_checker


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
