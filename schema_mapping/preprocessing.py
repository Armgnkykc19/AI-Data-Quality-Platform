from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_header(header: str) -> str:
    """Deterministic comparison form for source headers."""
    text = header.strip()
    text = unicodedata.normalize("NFC", text)
    text = text.casefold()
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = _NON_ALNUM.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ", "_")
