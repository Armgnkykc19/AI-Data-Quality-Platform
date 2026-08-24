from __future__ import annotations

import random
import re
from collections.abc import Callable

from dataset.manifest import CorruptionRecord

TURKISH_ASCII_MAP = {
    "ı": "i",
    "İ": "I",
    "ğ": "g",
    "Ğ": "G",
    "ü": "u",
    "Ü": "U",
    "ş": "s",
    "Ş": "S",
    "ö": "o",
    "Ö": "O",
    "ç": "c",
    "Ç": "C",
}

TYPOS = {
    "a": "e",
    "e": "a",
    "i": "e",
    "o": "u",
    "u": "o",
    "m": "n",
    "n": "m",
    "r": "l",
    "l": "r",
}


def _apply_case_change(value: str, rng: random.Random) -> str:
    mode = rng.choice(["upper", "lower", "title", "mixed"])
    if mode == "upper":
        return value.upper()
    if mode == "lower":
        return value.lower()
    if mode == "title":
        return value.title()
    return "".join(char.upper() if rng.random() < 0.5 else char.lower() for char in value)


def _apply_unicode_turkish(value: str, rng: random.Random) -> str:
    if rng.random() < 0.5:
        result = value
        for src, dst in TURKISH_ASCII_MAP.items():
            if rng.random() < 0.3:
                result = result.replace(src, dst)
        return result

    result = value
    for src, dst in TURKISH_ASCII_MAP.items():
        if dst in result and rng.random() < 0.2:
            result = result.replace(dst, src)
    return result


def _apply_whitespace(value: str, rng: random.Random) -> str:
    if rng.random() < 0.5:
        return re.sub(r"\s+", "  ", value.strip())
    return f"  {value.strip()}  "


def _apply_punctuation(value: str, rng: random.Random) -> str:
    if rng.random() < 0.5:
        return value.replace(" ", "-")
    return value.replace(".", ",")


def _apply_typo(value: str, rng: random.Random) -> str:
    chars = list(value)
    if not chars:
        return value
    index = rng.randrange(len(chars))
    char = chars[index].lower()
    if char in TYPOS:
        chars[index] = TYPOS[char]
    return "".join(chars)


def _apply_abbreviation(value: str, rng: random.Random) -> str:
    words = value.split()
    if len(words) < 2:
        return value
    index = rng.randrange(len(words))
    words[index] = words[index][:3] + "."
    return " ".join(words)


def _apply_phone_format(value: str, rng: random.Random) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 10:
        return value
    local = digits[-10:]
    formats = [
        f"0 ({local[:3]}) {local[3:6]} {local[6:8]} {local[8:]}",
        f"{local[:3]}-{local[3:6]}-{local[6:]}",
        f"({local[:3]}){local[3:6]}{local[6:]}",
        f"90{local}",
        f"+90 {local[:3]} {local[3:6]} {local[6:8]} {local[8:]}",
    ]
    return rng.choice(formats)


def _apply_email_corruption(value: str, rng: random.Random) -> str:
    if "@" not in value:
        return value
    local, domain = value.split("@", 1)
    mutations = [
        local.replace(".", ""),
        local.replace(".", "_"),
        f"{local}+legacy",
        local + domain.split(".")[0],
        f"{local}@{domain.replace('.', '')}.test",
    ]
    return rng.choice(mutations)


def _apply_missing_value(_value: str, _rng: random.Random) -> None:
    return None


def _apply_field_conflict(value: str, rng: random.Random) -> str:
    alternatives = [
        value + " Ltd.",
        value.replace("A.Ş.", "Ltd. Şti."),
        value + " (Merged)",
    ]
    return rng.choice(alternatives)


CORRUPTION_OPERATORS: dict[str, Callable[[str, random.Random], str | None]] = {
    "case_change": _apply_case_change,
    "unicode_turkish": _apply_unicode_turkish,
    "whitespace": _apply_whitespace,
    "punctuation": _apply_punctuation,
    "typo": _apply_typo,
    "abbreviation": _apply_abbreviation,
    "phone_format": _apply_phone_format,
    "email_corruption": _apply_email_corruption,
    "missing_value": _apply_missing_value,
    "field_conflict": _apply_field_conflict,
}


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    items = list(weights.items())
    total = sum(weight for _, weight in items)
    pick = rng.random() * total
    cumulative = 0.0
    for name, weight in items:
        cumulative += weight
        if pick <= cumulative:
            return name
    return items[-1][0]


def apply_corruption(
    *,
    corruption_type: str,
    field_name: str,
    value: str | None,
    rng: random.Random,
    person_id: str,
    source_record_id: str,
    source_name: str,
    severity: str,
) -> tuple[str | None, CorruptionRecord | None]:
    if value is None or corruption_type not in CORRUPTION_OPERATORS:
        return value, None

    operator = CORRUPTION_OPERATORS[corruption_type]
    after_value = operator(value, rng)

    if after_value == value:
        return value, None

    record = CorruptionRecord(
        corruption_type=corruption_type,
        field_name=field_name,
        before_value=value,
        after_value=after_value,
        severity=severity,
        person_id=person_id,
        source_record_id=source_record_id,
        source_name=source_name,
    )
    return after_value, record


def corrupt_record_fields(
    *,
    canonical: dict[str, str],
    profile: dict[str, object],
    severities: dict[str, str],
    rng: random.Random,
    person_id: str,
    source_record_id: str,
    source_name: str,
    allowed_fields: tuple[str, ...],
) -> tuple[dict[str, str | None], list[CorruptionRecord]]:
    field_rates: dict[str, float] = dict(profile.get("field_rates", {}))
    weights: dict[str, float] = dict(profile.get("corruption_weights", {}))
    max_corruptions = int(profile.get("max_corruptions_per_record", 3))

    fields: dict[str, str | None] = {key: canonical.get(key) for key in allowed_fields}
    corruptions: list[CorruptionRecord] = []

    target_fields = [
        field
        for field in allowed_fields
        if field in field_rates and rng.random() < field_rates[field]
    ]
    rng.shuffle(target_fields)
    target_fields = target_fields[:max_corruptions]

    for field_name in target_fields:
        if not weights:
            break
        corruption_type = weighted_choice(rng, weights)
        current = fields.get(field_name)
        updated, record = apply_corruption(
            corruption_type=corruption_type,
            field_name=field_name,
            value=current,
            rng=rng,
            person_id=person_id,
            source_record_id=source_record_id,
            source_name=source_name,
            severity=severities.get(corruption_type, "medium"),
        )
        fields[field_name] = updated
        if record is not None:
            corruptions.append(record)

    return fields, corruptions
