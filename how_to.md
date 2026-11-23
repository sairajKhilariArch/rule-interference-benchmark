# LLM Rule-Interaction Research Prototype (Development Only)

This starter project compares a local model and a cloud model on two tasks:
1. SQL generation under custom business rules.
2. Python function generation under custom requirements.

**This is only a development prototype.** The sample tasks and checks are visible development examples, not the sealed final benchmark. Never put final holdout prompts, gold SQL, scoring keys, or final result data in this folder.

## Arch Linux installation

```bash
sudo pacman -S --needed python python-pip
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally. Do not share it; it may contain an API key.

## Start local Colibri

In another terminal:

```bash
cd ~/colibri
./start-here.sh
```

Known local OpenAI-compatible API base: `http://127.0.0.1:8000/v1`

Check its model ID:

```bash
curl http://127.0.0.1:8000/v1/models
```

Copy the exact returned model ID into `LOCAL_MODEL` in `.env`.

## Configure cloud

For Nous Portal Qwen4 Max, set `CLOUD_BASE_URL=https://inference-api.nousresearch.com/v1`, `CLOUD_MODEL=qwen/qwen4-max:free`, and put your Nous Portal key in `CLOUD_API_KEY`. Confirm the exact model ID and endpoint in your Nous Portal account before use. Never paste an API key into chat or Git.

## Run the four plumbing tests

```bash
python run.py --provider local --task sql
python evaluate.py --kind sql --latest
python run.py --provider local --task python
python evaluate.py --kind python --latest

python run.py --provider cloud --task sql
python evaluate.py --kind sql --latest
python run.py --provider cloud --task python
python evaluate.py --kind python --latest
```

Model prompts and responses are stored in `runs/` as JSON. Evaluation reports are saved separately in `runs/evaluations/` as JSON. `.env` and `runs/` are excluded from Git so secrets and research data are not accidentally committed. Back up your research records privately.

## Safety and research warning

The Python checker runs generated code in a subprocess with a timeout, but this is **not a security sandbox**. Use only trusted model outputs, keep sensitive files and credentials out of reach, and do not treat this as safe for arbitrary code. Before the formal study, replace this with a proper isolated container/VM execution environment.

One successful run per model is only a plumbing test, not research evidence. The formal experiment needs frozen tasks, repeated runs, controlled settings, a sealed holdout set, and local scoring.
