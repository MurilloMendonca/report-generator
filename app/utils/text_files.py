"""Plain text validation and bounded document context."""

import os

MAX_TEXT_CONTEXT_CHARS = 20000


def read_text_file(path: str) -> tuple[str, str]:
    with open(path, "rb") as source:
        content = source.read()
    try:
        text, encoding = content.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        try:
            text, encoding = content.decode("cp1252"), "cp1252"
        except UnicodeDecodeError as exc:
            raise ValueError("TXT files must use UTF-8 or Windows-1252") from exc
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        raise ValueError("TXT file contains binary control characters")
    if not text.strip():
        raise ValueError("TXT file is empty")
    return text, encoding


def extract_text_metadata(path: str, display_name: str) -> dict:
    text, encoding = read_text_file(path)
    excerpt = text[:MAX_TEXT_CONTEXT_CHARS]
    truncated = len(text) > len(excerpt)
    return {
        "filename": display_name, "type": "text", "size_bytes": os.path.getsize(path),
        "encoding": encoding, "text_excerpt": excerpt, "text_preview": [excerpt[:1000]],
        "text_extraction": {
            "characters_extracted": len(excerpt), "truncated": truncated,
            "method": "plain_text", "quality": "usable",
            "warnings": ["Only the first 20,000 characters are used as document context."] if truncated else [],
        },
        "note": "Plain text is document context and is not loaded as a pandas dataframe.",
    }
