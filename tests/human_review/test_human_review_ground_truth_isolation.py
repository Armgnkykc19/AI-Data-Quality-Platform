from __future__ import annotations

import ast
from pathlib import Path


def test_human_review_package_does_not_import_evaluation_modules() -> None:
    root = Path("human_review")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("evaluation")
            ):
                offenders.append(f"{path}:{node.module}")
    assert not offenders


def test_production_modules_do_not_import_review_benchmark() -> None:
    for package in ("entity_resolution", "survivorship", "human_review"):
        root = Path(package)
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "evaluation.review_benchmark" not in source
            assert "evaluation.ground_truth" not in source
