from __future__ import annotations

from entity_resolution.engine import resolve_entities
from human_review.cases import generate_review_cases
from tests.human_review.conftest import make_record


def test_shuffled_input_produces_identical_review_cases(resolution_config) -> None:
    records = [
        make_record("rec-c", first_name="Ayse", last_name="Kaya", email="c@example.com"),
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="b@example.com"),
    ]
    resolution_a = resolve_entities(records, source_label="shuffled")
    resolution_b = resolve_entities(list(reversed(records)), source_label="shuffled")

    cases_a = generate_review_cases(resolution_a, config=resolution_config).cases
    cases_b = generate_review_cases(resolution_b, config=resolution_config).cases
    assert [case.review_case_id for case in cases_a] == [case.review_case_id for case in cases_b]
    assert [case.to_dict() for case in cases_a] == [case.to_dict() for case in cases_b]
