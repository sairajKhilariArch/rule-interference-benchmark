
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

def run_step(filename):
    print(f"\nRunning {filename}...\n", flush=True)

    return subprocess.run(
        [sys.executable, str(ROOT / "src" / filename)],
        cwd=ROOT
    ).returncode


def main():
    # Generation must succeed before evaluation.
    if run_step("generate_sql.py") != 0:
        print("SQL generation failed. Pipeline stopped.")
        sys.exit(1)

    # Evaluate generated SQL.
    evaluation_status = run_step("rule_checker.py")

    # Calculate metrics even if evaluation failed.
    metrics_status = run_step("metrics.py")

    if metrics_status != 0:
        print("Metrics calculation failed.")
        sys.exit(1)

    if evaluation_status != 0:
        print("\nPipeline completed with an evaluation failure.")
        print("The failure has been recorded locally.")
        sys.exit(2)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()
