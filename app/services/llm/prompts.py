"""Loading the Markdown prompt contracts in prompts/."""

import hashlib
import os


PROMPTS_DIR = "prompts"


def load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file {filename} not found.")
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def prompt_fingerprint(filename: str) -> dict:
    content = load_prompt(filename)
    return {
        "filename": filename,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "version": content.splitlines()[0].strip("# ").strip() if content.splitlines() else filename,
    }
