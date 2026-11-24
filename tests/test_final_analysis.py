from src.final_analysis import (
    exact_sign_flip_p_value,
    holm_correction,
    _paired_comparison,
)


def test_exact_sign_flip_p_value_handles_ties_and_direction():
    assert exact_sign_flip_p_value([0, 0]) == 1.0
    assert exact_sign_flip_p_value([1, 2]) == 0.5
    assert exact_sign_flip_p_value([1, -1]) == 1.0


def test_holm_correction_preserves_original_order():
    assert holm_correction([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def make_record(condition, trial, expected, actual, sql_hash):
    return {
        "condition": condition,
        "trial_number": trial,
        "status": "completed",
        "baseline": {
            "expected": expected,
            "actual": actual,
            "correct": expected == actual,
        },
        "sql_sha256": sql_hash,
    }


def test_paired_comparison_uses_matching_trial_numbers_and_error_magnitudes():
    records = [
        make_record("B_combined_rules", 1, 100, 120, "b1"),
        make_record("C_reordered_rules", 1, 100, 110, "c1"),
        make_record("B_combined_rules", 2, 100, 110, "b2"),
        make_record("C_reordered_rules", 2, 100, 120, "c2"),
    ]

    result = _paired_comparison(records)

    assert result["paired_trials"] == 2
    assert result["combined_worse"] == 1
    assert result["reordered_worse"] == 1
    assert result["equal_error"] == 0
    assert result["mean_combined_minus_reordered_pp"] == 0.0
    assert result["exact_sign_flip_p_value"] == 1.0
