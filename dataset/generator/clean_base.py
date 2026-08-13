from __future__ import annotations

import random
import re
from typing import Any

FIRST_NAMES = [
    "Ahmet",
    "Mehmet",
    "Ayşe",
    "Fatma",
    "Emre",
    "Elif",
    "Can",
    "Deniz",
    "Zeynep",
    "Burak",
    "Selin",
    "Oğuz",
    "Merve",
    "Kerem",
    "Ece",
    "Ali",
    "Hakan",
    "Gül",
    "Cem",
    "Derya",
]

LAST_NAMES = [
    "Yılmaz",
    "Kaya",
    "Demir",
    "Çelik",
    "Şahin",
    "Aydın",
    "Öztürk",
    "Arslan",
    "Koç",
    "Polat",
    "Güneş",
    "Aksoy",
    "Erdoğan",
    "Kurt",
    "Aslan",
    "Yıldız",
    "Doğan",
    "Özkan",
    "Tekin",
    "Karaca",
]

COMPANY_PREFIXES = [
    "Anadolu",
    "Marmara",
    "Ege",
    "Boğaziçi",
    "Atlas",
    "Nova",
    "Delta",
    "Zenith",
    "Pera",
    "Vega",
]

COMPANY_SUFFIXES = [
    "Teknoloji",
    "Danışmanlık",
    "Lojistik",
    "İnşaat",
    "Perakende",
    "Yazılım",
    "Enerji",
    "Sağlık",
    "Finans",
    "Ticaret",
]

CITIES = [
    "İstanbul",
    "Ankara",
    "İzmir",
    "Bursa",
    "Antalya",
    "Adana",
    "Konya",
    "Gaziantep",
    "Kocaeli",
    "Mersin",
]

DISTRICTS = [
    "Kadıköy",
    "Beşiktaş",
    "Çankaya",
    "Konak",
    "Nilüfer",
    "Muratpaşa",
    "Seyhan",
    "Selçuklu",
    "Şahinbey",
    "Mezitli",
]

STREET_NAMES = [
    "Atatürk",
    "Cumhuriyet",
    "İstiklal",
    "Barbaros",
    "Bağdat",
    "Moda",
    "Gazi",
    "Fatih",
    "Sanayi",
    "Park",
]

EMAIL_DOMAINS = [
    "example-mail.test",
    "demo-corp.test",
    "sample-org.test",
    "benchmark.test",
]


def _slugify(value: str) -> str:
    normalized = (
        value.lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    normalized = re.sub(r"[^a-z0-9]+", ".", normalized)
    return normalized.strip(".")


def _format_person_id(index: int) -> str:
    return f"P-{index:06d}"


def _format_phone(index: int) -> str:
    suffix = f"{index:09d}"
    return f"+905{suffix[:2]}{suffix[2:5]}{suffix[5:7]}{suffix[7:9]}"


def generate_clean_base(*, seed: int, record_count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    used_emails: set[str] = set()
    used_phones: set[str] = set()

    for index in range(1, record_count + 1):
        first_name = rng.choice(FIRST_NAMES)
        last_name = rng.choice(LAST_NAMES)
        company = f"{rng.choice(COMPANY_PREFIXES)} {rng.choice(COMPANY_SUFFIXES)} A.Ş."
        city = rng.choice(CITIES)
        district = rng.choice(DISTRICTS)
        street = rng.choice(STREET_NAMES)
        building_no = rng.randint(1, 250)
        address = f"{street} Cad. No:{building_no}, {district}/{city}"

        email_base = f"{_slugify(first_name)}.{_slugify(last_name)}"
        domain = rng.choice(EMAIL_DOMAINS)
        email = f"{email_base}{index}@{domain}"
        while email in used_emails:
            email = f"{email_base}{index}x{rng.randint(1, 999)}@{domain}"
        used_emails.add(email)

        phone = _format_phone(index)
        while phone in used_phones:
            phone = _format_phone(index + len(used_phones) + 1)
        used_phones.add(phone)

        records.append(
            {
                "person_id": _format_person_id(index),
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "company": company,
                "city": city,
                "district": district,
                "address": address,
            }
        )

    return records
