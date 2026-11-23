import os
import requests
from dotenv import load_dotenv

load_dotenv()

def ask_model(provider: str, prompt: str):
    prefix = "LOCAL" if provider == "local" else "CLOUD"
    base = os.getenv(f"{prefix}_BASE_URL", "").rstrip("/")
    key = os.getenv(f"{prefix}_API_KEY", "")
    model = os.getenv(f"{prefix}_MODEL", "")
    if not base or not model or "REPLACE_WITH" in model:
        raise RuntimeError(f"Configure {prefix}_BASE_URL and {prefix}_MODEL in .env.")
    if provider == "cloud" and (not key or "PASTE_YOUR_KEY" in key):
        raise RuntimeError("Set CLOUD_API_KEY in your local .env.")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Follow every requirement. Return only the requested code."},
            {"role": "user", "content": prompt}
        ],
        "temperature": float(os.getenv("TEMPERATURE", "0")),
        "max_tokens": int(os.getenv("MAX_TOKENS", "1200"))
    }
    response = requests.post(
        f"{base}/chat/completions", headers=headers, json=payload,
        timeout=int(os.getenv("REQUEST_TIMEOUT", "120"))
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"], model
