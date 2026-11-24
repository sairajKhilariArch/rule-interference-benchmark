
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"


def wilson_interval(successes, total, z=1.96):
    """Calculate a 95% Wilson confidence interval."""

    if total == 0:
        return None

    p = successes / total
    denominator = 1 + z * z / total

    center = (
        p + z * z / (2 * total)
    ) / denominator

    margin = (
        z * math.sqrt(
            p * (1 - p) / total
            + z * z / (4 * total * total)
        ) / denominator
    )

    return [
        round(max(0, center - margin) * 100, 2),
        round(min(1, center + margin) * 100, 2)
    ]


def exact_mcnemar(improved, worsened):
    """Calculate the two-sided exact McNemar p-value."""

    discordant = improved + worsened

    if discordant == 0:
        return 1.0

    smaller = min(improved, worsened)

    probability = sum(
        math.comb(discordant, k)
        for k in range(smaller + 1)
    ) / (2 ** discordant)

    return min(1.0, 2 * probability)


def holm_correction(p_values):
    """Apply Holm-Bonferroni correction to multiple p-values."""

    if not p_values:
        return []

    indexed = sorted(
        enumerate(p_values),
        key=lambda item: item[1]
    )

    count = len(indexed)
    adjusted = [None] * count
    running_max = 0.0

    for rank, (original_index, p_value) in enumerate(indexed):
        corrected = min(
            1.0,
            (count - rank) * p_value
        )

        running_max = max(running_max, corrected)

        adjusted[original_index] = running_max

    return adjusted


def analyze_condition(values):
    """Calculate compliance statistics for one condition."""

    completed = values["evaluation_completed"]
    compliant = values["fully_compliant"]

    return {
        "evaluations_completed": completed,
        "fully_compliant": compliant,
        "compliance_rate_percent": (
            round(compliant / completed * 100, 2)
            if completed else None
        ),
        "wilson_95_percent_interval": wilson_interval(
            compliant, completed
        )
    }


def analyze_comparison(comparison):
    """Analyze paired canonical versus reordered results."""

    paired = comparison["paired_results"]

    improved = sum(
        not item["canonical_compliant"]
        and item["reordered_compliant"]
        for item in paired
    )

    worsened = sum(
        item["canonical_compliant"]
        and not item["reordered_compliant"]
        for item in paired
    )

    unchanged = len(paired) - improved - worsened

    p_value = exact_mcnemar(improved, worsened)

    return {
        "paired_trials": len(paired),
        "improved_after_reordering": improved,
        "worsened_after_reordering": worsened,
        "unchanged": unchanged,
        "exact_mcnemar_p_value": p_value,
        "canonical_compliance_percent": (
            comparison["canonical_rate_percent"]
        ),
        "reordered_compliance_percent": (
            comparison["reordered_rate_percent"]
        ),
        "difference_percentage_points": (
            comparison["difference_percentage_points"]
        ),
        "holm_adjusted_p_value": None,
        "significant_after_holm_correction": None
    }


def main():

    runs = sorted(
        [
            path for path in RUNS_DIR.iterdir()
            if path.is_dir()
            and (path / "metrics_summary.json").exists()
        ],
        key=lambda path: path.name
    )

    if not runs:
        raise SystemExit("No analysis summary found.")

    run_dir = runs[-1]

    data = json.loads(
        (run_dir / "metrics_summary.json").read_text(
            encoding="utf-8"
        )
    )

    if "groups" not in data:
        raise SystemExit(
            "This analysis requires the updated grouped "
            "metrics_summary.json. Run analyze_run.py first."
        )

    results = {
        "run_id": data["run_id"],
        "model": data.get("model"),
        "temperature": data.get("temperature"),
        "trials_per_condition": data.get(
            "trials_per_condition"
        ),
        "planned_generations": data.get(
            "planned_generations"
        ),
        "group_statistics": {},
        "multiple_comparison_method": "Holm-Bonferroni",
        "significance_level": 0.05
    }

    report = [
        "# Statistical Analysis",
        "",
        f"Experiment run: {data['run_id']}",
        f"Model: {data.get('model')}",
        f"Temperature: {data.get('temperature')}",
        f"Trials per condition: "
        f"{data.get('trials_per_condition')}",
        "",
        "## Condition-wise compliance",
        "",
        "Compliance rates are reported with 95% Wilson "
        "confidence intervals.",
        ""
    ]

    comparison_keys = []
    comparison_p_values = []

    # Analyze each schema and dataset-size group.
    for group_name, group in data["groups"].items():

        group_result = {
            "schema_profile": group["schema_profile"],
            "dataset_size": group["dataset_size"],
            "condition_statistics": {},
            "canonical_vs_reordered": None
        }

        report.extend([
            f"# {group['schema_profile'].upper()} — "
            f"{group['dataset_size']} orders",
            ""
        ])

        # Condition statistics.
        for condition, values in group["conditions"].items():

            statistics = analyze_condition(values)

            group_result["condition_statistics"][
                condition
            ] = statistics

            interval = statistics[
                "wilson_95_percent_interval"
            ]

            report.extend([
                f"## {condition}",
                f"- Evaluations completed: "
                f"{statistics['evaluations_completed']}",
                f"- Fully compliant: "
                f"{statistics['fully_compliant']}",
                f"- Compliance rate: "
                f"{statistics['compliance_rate_percent']}%",
                f"- 95% Wilson interval: {interval}",
                ""
            ])

        # Paired canonical versus reordered comparison.
        comparison = group["canonical_vs_reordered"]

        comparison_result = analyze_comparison(comparison)

        group_result["canonical_vs_reordered"] = (
            comparison_result
        )

        paired_count = comparison_result["paired_trials"]

        if paired_count > 0:
            comparison_keys.append(group_name)
            comparison_p_values.append(
                comparison_result["exact_mcnemar_p_value"]
            )

        report.extend([
            "## Canonical versus reordered rules",
            f"- Paired trials: {paired_count}",
            f"- Improved after reordering: "
            f"{comparison_result['improved_after_reordering']}",
            f"- Worsened after reordering: "
            f"{comparison_result['worsened_after_reordering']}",
            f"- Unchanged: "
            f"{comparison_result['unchanged']}",
            f"- Exact McNemar p-value: "
            f"{comparison_result['exact_mcnemar_p_value']:.8g}",
            ""
        ])

        results["group_statistics"][group_name] = group_result

    # Correct the six paired comparisons for multiple testing.
    adjusted_values = holm_correction(comparison_p_values)

    for group_name, adjusted_p in zip(
        comparison_keys,
        adjusted_values
    ):

        comparison = results["group_statistics"][
            group_name
        ]["canonical_vs_reordered"]

        comparison["holm_adjusted_p_value"] = adjusted_p

        comparison["significant_after_holm_correction"] = (
            adjusted_p <= 0.05
        )

    report.extend([
        "# Multiple comparison correction",
        "",
        "Holm-Bonferroni correction was applied to the "
        "paired canonical-versus-reordered comparisons "
        "with available paired trials.",
        "",
        "| Group | Raw p-value | Adjusted p-value | Significant at 0.05 |",
        "|---|---:|---:|---|"
    ])

    for group_name in comparison_keys:

        comparison = results["group_statistics"][
            group_name
        ]["canonical_vs_reordered"]

        report.append(
            f"| {group_name} "
            f"| {comparison['exact_mcnemar_p_value']} "
            f"| {comparison['holm_adjusted_p_value']} "
            f"| {comparison['significant_after_holm_correction']} |"
        )

    report.extend([
        "",
        "# Interpretation and limitations",
        "",
        "- Compliance rates describe the observed experiment results.",
        "- Wilson intervals quantify uncertainty around compliance rates.",
        "- Exact McNemar tests compare paired canonical and reordered trials.",
        "- Holm-Bonferroni correction controls family-wise error "
        "across the available paired comparisons.",
        "- A non-significant result does not prove that rule order "
        "has no effect.",
        "- Ten trials per condition provide limited statistical power.",
        "- Results apply only to the tested model, prompts, schemas "
        "and generated datasets.",
        "- Independent replication with additional models and trials "
        "is recommended.",
        "- SQL execution failures and result-contract failures should "
        "be reported separately from semantic rule violations."
    ])

    (run_dir / "statistical_analysis.json").write_text(
        json.dumps(results, indent=4),
        encoding="utf-8"
    )

    (run_dir / "statistical_analysis.md").write_text(
        "\n".join(report),
        encoding="utf-8"
    )

    print("Statistical analysis completed.")
    print("Run:", data["run_id"])
    print(
        "Groups analyzed:",
        len(results["group_statistics"])
    )
    print(
        "Paired comparisons:",
        len(comparison_keys)
    )
    print("Holm-Bonferroni correction applied.")
    print("Statistical files saved locally.")


if __name__ == "__main__":
    main()
