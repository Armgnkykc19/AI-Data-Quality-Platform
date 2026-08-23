from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    text = unicodedata.normalize("NFC", text)
    text = " ".join(text.split())
    return text.casefold()


def normalize_email(value: str | None) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    return text


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return None
    return digits


def fuzzy_similarity(left: str | None, right: str | None) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if left_norm is None or right_norm is None:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def is_non_empty(value: str | None) -> bool:
    return value is not None and str(value).strip() != ""
