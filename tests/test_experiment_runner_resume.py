import json
import sys

from pathlib import Path
from types import SimpleNamespace

import pytest

from src import experiment_runner as runner


@pytest.mark.parametrize(
    ("evaluator_status", "expected_final_status"),
    [
        ("completed", "completed"),
        ("sql_execution_failure", "completed_with_failures"),
    ],
)
def test_resume_recovers_evaluator_record_without_duplicate(
    tmp_path,
    monkeypatch,
    evaluator_status,
    expected_final_status,
):
    # Everything is redirected to a temporary test directory.
    data_dir = tmp_path / "datasets"
    results_dir = tmp_path / "experiments"
    data_dir.mkdir()
    results_dir.mkdir()

    dataset = data_dir / "baseline_100_orders.db"
    dataset.touch()

    log_path = tmp_path / "rule_test_log.jsonl"
    config_path = tmp_path / "conditions.json"

    config = {
        "model": "test-model",
        "temperature": 0,
        "random_seed": 123,
        "trials_per_condition": 1,
        "dataset_sizes": [100],
        "schema_profiles": ["baseline"],
        "question": "Test question",
        "conditions": [
            {
                "name": "test_condition",
                "rules": ["Test rule"]
            }
        ],
    }

    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runner, "DATASET_DIR", data_dir)
    monkeypatch.setattr(runner, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    generation_calls = []
    evaluation_calls = []

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    generation_calls.append(1)
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="SELECT 1;")
                            )
                        ]
                    )

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(runner, "client", FakeClient())

    def fake_evaluator(args, cwd):
        evaluation_calls.append(1)

        record = {
            "condition": args[3],
            "run_id": args[4],
            "trial_number": int(args[5]),
            "schema_profile": args[7],
            "dataset_size": int(args[8]),
            "status": evaluator_status,
            "error": (
                "Simulated SQL failure"
                if evaluator_status != "completed"
                else None
            ),
        }

        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")

        # Simulate a crash immediately after evaluator logging.
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "run", fake_evaluator)

    # First execution: simulate interruption after evaluator logging.
    monkeypatch.setattr(sys, "argv", ["experiment_runner.py"])

    with pytest.raises(SystemExit) as interruption:
        runner.main()

    assert interruption.value.code == 130

    run_directories = list(results_dir.iterdir())
    assert len(run_directories) == 1

    run_id = run_directories[0].name

    # Second execution: resume from the saved evaluator record.
    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--resume", run_id],
    )

    runner.main()

    metadata = json.loads(
        (results_dir / run_id / "metadata.json").read_text()
    )

    # The generation and evaluation must not be repeated.
    assert len(generation_calls) == 1
    assert len(evaluation_calls) == 1

    assert metadata["evaluated_generations"] == 1
    assert metadata["pending_generations"] == 0
    assert metadata["status"] == expected_final_status

    assert len(log_path.read_text().splitlines()) == 1


def test_resume_reuses_saved_sql_when_evaluator_record_is_missing(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "datasets"
    results_dir = tmp_path / "experiments"
    data_dir.mkdir()
    results_dir.mkdir()

    (data_dir / "baseline_100_orders.db").touch()

    log_path = tmp_path / "rule_test_log.jsonl"
    config_path = tmp_path / "conditions.json"

    config = {
        "model": "test-model",
        "temperature": 0,
        "random_seed": 123,
        "trials_per_condition": 1,
        "dataset_sizes": [100],
        "schema_profiles": ["baseline"],
        "question": "Test question",
        "conditions": [
            {"name": "test_condition", "rules": ["Test rule"]}
        ],
    }

    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runner, "DATASET_DIR", data_dir)
    monkeypatch.setattr(runner, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    generation_calls = []

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    generation_calls.append(1)
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content="SELECT 1;"
                                )
                            )
                        ]
                    )

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(runner, "client", FakeClient())

    evaluation_calls = []

    def interrupted_evaluator(args, cwd):
        evaluation_calls.append("interrupted")
        raise KeyboardInterrupt

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        interrupted_evaluator,
    )

    monkeypatch.setattr(sys, "argv", ["experiment_runner.py"])

    with pytest.raises(SystemExit) as interruption:
        runner.main()

    assert interruption.value.code == 130
    assert len(generation_calls) == 1

    run_directories = list(results_dir.iterdir())
    assert len(run_directories) == 1
    run_id = run_directories[0].name

    sql_files = list(run_directories[0].glob("*.sql"))
    assert len(sql_files) == 1
    assert sql_files[0].read_text() == "SELECT 1;"

    def successful_evaluator(args, cwd):
        evaluation_calls.append("resumed")

        record = {
            "run_id": args[4],
            "trial_number": int(args[5]),
            "condition": args[3],
            "schema_profile": args[7],
            "dataset_size": int(args[8]),
            "status": "completed",
            "error": None,
        }

        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")

        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        successful_evaluator,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--resume", run_id],
    )

    runner.main()

    metadata = json.loads(
        (results_dir / run_id / "metadata.json").read_text()
    )

    assert len(generation_calls) == 1
    assert evaluation_calls == ["interrupted", "resumed"]
    assert metadata["evaluated_generations"] == 1
    assert metadata["successful_evaluations"] == 1
    assert metadata["pending_generations"] == 0
    assert metadata["status"] == "completed"

    condition = metadata["trials"][0]["conditions"]["test_condition"]
    assert condition["reused_sql_file"] is True


def test_generation_retries_then_succeeds(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "datasets"
    results_dir = tmp_path / "experiments"
    data_dir.mkdir()
    results_dir.mkdir()

    (data_dir / "baseline_100_orders.db").touch()

    log_path = tmp_path / "rule_test_log.jsonl"
    config_path = tmp_path / "conditions.json"

    config = {
        "model": "test-model",
        "temperature": 0,
        "random_seed": 123,
        "trials_per_condition": 1,
        "dataset_sizes": [100],
        "schema_profiles": ["baseline"],
        "question": "Test question",
        "conditions": [
            {"name": "test_condition", "rules": ["Test rule"]}
        ],
    }

    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runner, "DATASET_DIR", data_dir)
    monkeypatch.setattr(runner, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(runner, "LOG_PATH", log_path)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    generation_calls = []

    class FakeClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    generation_calls.append(1)

                    if len(generation_calls) < 3:
                        raise RuntimeError("Simulated temporary failure")

                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="SELECT 1;")
                            )
                        ]
                    )

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(runner, "client", FakeClient())

    evaluation_calls = []

    def successful_evaluator(args, cwd):
        evaluation_calls.append(1)

        record = {
            "condition": args[3],
            "run_id": args[4],
            "trial_number": int(args[5]),
            "schema_profile": args[7],
            "dataset_size": int(args[8]),
            "status": "completed",
            "error": None,
        }

        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")

        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        successful_evaluator,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--retries", "2"],
    )

    runner.main()

    run_directory = next(results_dir.iterdir())
    metadata = json.loads(
        (run_directory / "metadata.json").read_text()
    )

    condition = metadata["trials"][0]["conditions"]["test_condition"]

    assert len(generation_calls) == 3
    assert len(evaluation_calls) == 1
    assert condition["generation"] == "completed"
    assert condition["evaluation"] == "completed"
    assert "error" not in condition
    assert metadata["generation_failures"] == 0
    assert metadata["pending_generations"] == 0
    assert metadata["status"] == "completed"


def test_failed_generation_can_be_retried_after_resume(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "datasets"
    results_dir = tmp_path / "experiments"
    data_dir.mkdir()
    results_dir.mkdir()

    (data_dir / "baseline_100_orders.db").touch()

    log_path = tmp_path / "rule_test_log.jsonl"
    config_path = tmp_path / "conditions.json"

    config = {
        "model": "test-model",
        "temperature": 0,
        "random_seed": 123,
        "trials_per_condition": 1,
        "dataset_sizes": [100],
        "schema_profiles": ["baseline"],
        "question": "Test question",
        "conditions": [
            {"name": "test_condition", "rules": ["Test rule"]}
        ],
    }

    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runner, "DATASET_DIR", data_dir)
    monkeypatch.setattr(runner, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(runner, "LOG_PATH", log_path)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    generation_calls = []

    class FailingClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    generation_calls.append("failed")
                    raise RuntimeError("Simulated API outage")

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(runner, "client", FailingClient())
    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--retries", "1"],
    )

    # First run: all generation attempts fail.
    runner.main()

    run_directory = next(results_dir.iterdir())
    run_id = run_directory.name

    metadata = json.loads(
        (run_directory / "metadata.json").read_text()
    )

    condition = metadata["trials"][0]["conditions"]["test_condition"]

    assert len(generation_calls) == 2
    assert condition["generation"] == "failed"
    assert condition["evaluation"] == "not_run"
    assert metadata["generation_failures"] == 1
    assert metadata["pending_generations"] == 1
    assert metadata["status"] == "incomplete"
    assert not list(run_directory.glob("*.sql"))
    assert not log_path.exists()

    # Second run: API recovers.
    class RecoveredClient:
        class Chat:
            class Completions:
                @staticmethod
                def create(**kwargs):
                    generation_calls.append("recovered")
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="SELECT 1;")
                            )
                        ]
                    )

            completions = Completions()

        chat = Chat()

    monkeypatch.setattr(runner, "client", RecoveredClient())

    def successful_evaluator(args, cwd):
        record = {
            "condition": args[3],
            "run_id": args[4],
            "trial_number": int(args[5]),
            "schema_profile": args[7],
            "dataset_size": int(args[8]),
            "status": "completed",
            "error": None,
        }

        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")

        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        successful_evaluator,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--resume", run_id, "--retries", "1"],
    )

    runner.main()

    metadata = json.loads(
        (run_directory / "metadata.json").read_text()
    )

    condition = metadata["trials"][0]["conditions"]["test_condition"]

    assert len(generation_calls) == 3
    assert condition["generation"] == "completed"
    assert condition["evaluation"] == "completed"
    assert "error" not in condition
    assert metadata["generation_failures"] == 0
    assert metadata["successful_evaluations"] == 1
    assert metadata["pending_generations"] == 0
    assert metadata["status"] == "completed"


def test_resume_rejects_changed_experiment_configuration(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "datasets"
    results_dir = tmp_path / "experiments"
    data_dir.mkdir()
    results_dir.mkdir()

    (data_dir / "baseline_100_orders.db").touch()

    log_path = tmp_path / "rule_test_log.jsonl"
    config_path = tmp_path / "conditions.json"

    original_config = {
        "model": "test-model",
        "temperature": 0,
        "random_seed": 123,
        "trials_per_condition": 1,
        "dataset_sizes": [100],
        "schema_profiles": ["baseline"],
        "question": "Original question",
        "conditions": [
            {"name": "test_condition", "rules": ["Test rule"]}
        ],
    }

    changed_config = dict(original_config)
    changed_config["question"] = "Changed question"

    config_path.write_text(
        json.dumps(changed_config),
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runner, "DATASET_DIR", data_dir)
    monkeypatch.setattr(runner, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(runner, "LOG_PATH", log_path)

    run_id = "configuration_protection_test"
    run_directory = results_dir / run_id
    run_directory.mkdir()

    snapshot = runner.config_snapshot(original_config, 1)

    metadata = {
        "checkpoint_version": runner.CHECKPOINT_VERSION,
        "run_id": run_id,
        "planned_generations": 1,
        "config_sha256": runner.snapshot_hash(snapshot),
        "trials": [],
    }

    (run_directory / "metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["experiment_runner.py", "--resume", run_id],
    )

    with pytest.raises(ValueError, match="configuration has changed"):
        runner.main()

    assert not log_path.exists()


def test_update_counts_tracks_all_progress_categories():
    run_id = "accounting_test"

    metadata = {
        "run_id": run_id,
        "planned_generations": 3,
        "trials": [
            {
                "schema_profile": "baseline",
                "dataset_size": 100,
                "trial_number": 1,
                "conditions": {
                    "condition_a": {"generation": "completed"},
                    "condition_b": {"generation": "completed"},
                    "condition_c": {"generation": "failed"},
                },
            }
        ],
    }

    evaluator_index = {
        (run_id, "baseline", 100, 1, "condition_a"): {
            "status": "completed"
        },
        (run_id, "baseline", 100, 1, "condition_b"): {
            "status": "sql_execution_failure"
        },
    }

    runner.update_counts(metadata, evaluator_index)

    assert metadata["successful_evaluations"] == 1
    assert metadata["failed_evaluations"] == 1
    assert metadata["generation_failures"] == 1
    assert metadata["failed_conditions"] == 2
    assert metadata["evaluated_generations"] == 2
    assert metadata["pending_generations"] == 1


def test_update_counts_does_not_double_count_recovered_generation():
    run_id = "recovered_accounting_test"

    metadata = {
        "run_id": run_id,
        "planned_generations": 1,
        "trials": [
            {
                "schema_profile": "baseline",
                "dataset_size": 100,
                "trial_number": 1,
                "conditions": {
                    "condition_a": {"generation": "failed"},
                },
            }
        ],
    }

    evaluator_index = {
        (run_id, "baseline", 100, 1, "condition_a"): {
            "status": "completed"
        }
    }

    runner.update_counts(metadata, evaluator_index)

    assert metadata["successful_evaluations"] == 1
    assert metadata["generation_failures"] == 0
    assert metadata["evaluated_generations"] == 1
    assert metadata["pending_generations"] == 0


def test_update_counts_rejects_more_records_than_planned():
    metadata = {
        "run_id": "invalid_accounting_test",
        "planned_generations": 1,
        "trials": [],
    }

    evaluator_index = {
        ("invalid_accounting_test", "baseline", 100, 1, "condition_a"): {
            "status": "completed"
        },
        ("invalid_accounting_test", "baseline", 100, 1, "condition_b"): {
            "status": "completed"
        },
    }

    with pytest.raises(ValueError, match="exceed planned generations"):
        runner.update_counts(metadata, evaluator_index)
