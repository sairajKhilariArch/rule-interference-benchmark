import argparse, json, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path

def latest_run(kind):
    files = sorted(Path("runs").glob(f"*_{kind}.json"))
    if not files:
        raise SystemExit(f"No saved {kind} run found. Run run.py first.")
    return files[-1]

def strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:sql|python)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def evaluate_sql(code):
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, verified INTEGER, deleted INTEGER);
    CREATE TABLE orders(order_id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
    CREATE TABLE order_items(item_id INTEGER PRIMARY KEY, order_id INTEGER, quantity INTEGER, unit_price REAL);
    CREATE TABLE refunds(refund_id INTEGER PRIMARY KEY, order_id INTEGER, refund_amount REAL);
    INSERT INTO customers VALUES (1,1,0),(2,0,0),(3,1,1),(4,1,0);
    INSERT INTO orders VALUES (10,1,'2026-01-05','paid'),(11,2,'2026-01-06','paid'),(12,3,'2026-01-07','paid'),(13,4,'2026-02-01','paid');
    INSERT INTO order_items VALUES (1,10,2,10.0),(2,10,1,5.0),(3,11,1,100.0),(4,12,1,70.0),(5,13,1,30.0);
    INSERT INTO refunds VALUES (1,10,4.0);
    """)
    try:
        row = con.execute(code).fetchone()
        if row is None:
            return {"sql_executes": True, "business_checks_pass": False, "reason": "No result row"}
        observed = float(row[0])
        # Development-only expected value; never reuse this as a final-study answer key.
        passed = abs(observed - 21.0) < 0.001
        return {"sql_executes": True, "business_checks_pass": passed, "observed_value": observed}
    except Exception as exc:
        return {"sql_executes": False, "business_checks_pass": False, "error": str(exc)}
    finally:
        con.close()

def evaluate_python(code):
    harness = (
        "from solution import discount_total\n"
        "assert round(float(discount_total(100,10,True)),2) == 90.00\n"
        "assert round(float(discount_total(100,10,False)),2) == 100.00\n"
        "assert round(float(discount_total(19.99,15,True)),2) == 16.99\n"
        "for args in [(-1,10,True),(10,-1,True),(10,101,True)]:\n"
        "    try: discount_total(*args)\n"
        "    except ValueError: pass\n"
        "    else: raise AssertionError('Expected ValueError')\n"
        "print('DEV_TESTS_PASSED')\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "solution.py").write_text(strip_fences(code))
        (p / "test_runner.py").write_text(harness)
        try:
            result = subprocess.run(
                [sys.executable, "test_runner.py"], cwd=tmp,
                capture_output=True, text=True, timeout=4,
                env={"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"}
            )
            passed = result.returncode == 0 and "DEV_TESTS_PASSED" in result.stdout
            return {"all_dev_tests_pass": passed,
                    "stdout": result.stdout[-800:], "stderr": result.stderr[-800:],
                    "warning": "A subprocess timeout is not a security sandbox."}
        except subprocess.TimeoutExpired:
            return {"all_dev_tests_pass": False, "error": "Timed out after 4 seconds"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["sql", "python"], required=True)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--file")
    args = parser.parse_args()
    path = Path(args.file) if args.file else latest_run(args.kind)
    record = json.loads(path.read_text())
    if record["kind"] != args.kind:
        raise SystemExit("Selected run has the wrong task type.")
    code = strip_fences(record["output"])
    result = evaluate_sql(code) if args.kind == "sql" else evaluate_python(code)
    report = {
        "provider": record["provider"], "model": record["model"],
        "kind": args.kind, "task_name": record.get("task_name"),
        "run_timestamp_utc": record.get("timestamp_utc"),
        "latency_seconds": record["latency_seconds"],
        "evaluation": result
    }
    report_dir = Path("runs") / "evaluations"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{path.stem}_evaluation.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\\nSaved evaluation report: {report_path}")

if __name__ == "__main__":
    main()
