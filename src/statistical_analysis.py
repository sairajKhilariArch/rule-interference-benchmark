
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "experiments"


def wilson_interval(successes, total, z=1.96):
    if total == 0:
        return None

    p = successes / total
    denominator = 1 + z * z / total

    center = (p + z * z / (2 * total)) / denominator

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
    discordant = improved + worsened

    if discordant == 0:
        return 1.0

    smaller = min(improved, worsened)

    probability = sum(
        math.comb(discordant, k)
        for k in range(smaller + 1)
    ) / (2 ** discordant)

    return min(1.0, 2 * probability)


def main():
    runs = sorted(
        [
            p for p in RUNS_DIR.iterdir()
            if p.is_dir() and (p / "metrics_summary.json").exists()
        ],
        key=lambda p: p.name
    )

    if not runs:
        raise SystemExit("No analysis summary found.")

    run_dir = runs[-1]

    data = json.loads(
        (run_dir / "metrics_summary.json").read_text()
    )

    results = {
        "run_id": data["run_id"],
        "condition_statistics": {},
        "canonical_vs_reordered": {}
    }

    report = [
        "# Statistical Analysis",
        "",
        f"Experiment run: {data['run_id']}",
        "",
        "## Condition-wise compliance",
        ""
    ]

    for condition, values in data["conditions"].items():
        completed = values["evaluation_completed"]
        compliant = values["fully_compliant"]

        interval = wilson_interval(compliant, completed)

        results["condition_statistics"][condition] = {
            "completed": completed,
            "compliant": compliant,
            "compliance_rate_percent": (
                round(compliant / completed * 100, 2)
                if completed else None
            ),
            "wilson_95_percent_interval": interval
        }

        report.extend([
            f"### {condition}",
            f"- Evaluations completed: {completed}",
            f"- Fully compliant: {compliant}",
            f"- Compliance rate: "
            f"{round(compliant / completed * 100, 2) if completed else 'N/A'}%",
            f"- 95% Wilson interval: {interval}",
            ""
        ])

    comparison = data["canonical_vs_reordered"]
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

    results["canonical_vs_reordered"] = {
        "paired_trials": len(paired),
        "improved_after_reordering": improved,
        "worsened_after_reordering": worsened,
        "unchanged": unchanged,
        "exact_mcnemar_p_value": round(p_value, 6),
        "interpretation": (
            "Exploratory comparison only. The sample is too small "
            "to establish a reliable general conclusion."
        )
    }

    report.extend([
        "## Canonical versus reordered rules",
        f"- Paired trials: {len(paired)}",
        f"- Improved after reordering: {improved}",
        f"- Worsened after reordering: {worsened}",
        f"- Unchanged: {unchanged}",
        f"- Exact McNemar p-value: {p_value:.6f}",
        "",
        "## Interpretation",
        "",
        "This is an exploratory paired comparison. A small sample "
        "does not provide sufficient evidence for a general "
        "conclusion about rule-order sensitivity.",
        "",
        "## Limitations",
        "",
        "- Only the tested model and configuration were evaluated.",
        "- Five trials per condition provide limited statistical power.",
        "- Results are specific to the benchmark schema and dataset.",
        "- Execution failures and result-contract failures must be "
        "reported separately from executable rule violations.",
        "- Further trials and independent replication are needed."
    ])

    (run_dir / "statistical_analysis.json").write_text(
        json.dumps(results, indent=4)
    )

    (run_dir / "statistical_analysis.md").write_text(
        "\n".join(report)
    )

    print("Statistical analysis completed.")
    print("Run:", data["run_id"])
    print("Paired trials:", len(paired))
    print("Exact McNemar test calculated.")
    print("Statistical files saved locally.")


if __name__ == "__main__":
    main()
