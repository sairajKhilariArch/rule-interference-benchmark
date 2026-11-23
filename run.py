import argparse, json, time
from pathlib import Path
from datetime import datetime, timezone
from model_client import ask_model

TASKS = {
    "sql": Path("tasks/sql_dev.json"),
    "python": Path("tasks/python_dev.json")
}

def build_prompt(task, kind):
    rules = "\\n".join("- " + item for item in task["rules"])
    if kind == "sql":
        return f"Database schema:\\n{task['schema']}\\nBusiness rules:\\n{rules}\\nRequest:\\n{task['question']}\\nReturn only one SQLite SELECT query. No markdown fences."
    return f"Requirements:\\n{rules}\\nProgramming task:\\n{task['question']}\\nReturn only the Python function. No markdown fences."

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["local", "cloud"], required=True)
    parser.add_argument("--task", choices=["sql", "python"], required=True)
    args = parser.parse_args()
    task = json.loads(TASKS[args.task].read_text())
    prompt = build_prompt(task, args.task)
    start = time.perf_counter()
    output, model = ask_model(args.provider, prompt)
    elapsed = time.perf_counter() - start
    record = {
        "provider": args.provider, "model": model, "kind": args.task,
        "task_name": task["name"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "latency_seconds": round(elapsed, 3),
        "prompt": prompt, "output": output
    }
    Path("runs").mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path("runs") / f"{stamp}_{args.provider}_{args.task}.json"
    path.write_text(json.dumps(record, indent=2))
    print(f"Saved: {path}")
    print(f"Model: {model} | Latency: {elapsed:.2f}s")
    print("\\n--- MODEL OUTPUT ---\\n")
    print(output)

if __name__ == "__main__":
    main()
