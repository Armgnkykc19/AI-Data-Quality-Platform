from __future__ import annotations

import ast
from pathlib import Path

PRODUCTION_PACKAGES = (
    "entity_resolution",
    "survivorship",
    "human_review",
    "ingestion",
    "validation",
    "normalization",
    "schema_mapping",
)


def test_production_packages_do_not_import_openai_or_semantic_review() -> None:
    offenders: list[str] = []
    for package in PRODUCTION_PACKAGES:
        root = Path(package)
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "from openai" not in source
            assert "import openai" not in source
            assert "evaluation.semantic_review_benchmark" not in source
            assert "evaluation.ground_truth" not in source
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("semantic_review"):
                        offenders.append(f"{path}:{node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "openai" or alias.name.startswith("openai."):
                            offenders.append(f"{path}:{alias.name}")
    assert not offenders


def test_human_review_does_not_import_semantic_review() -> None:
    for path in Path("human_review").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "semantic_review" not in source


def test_semantic_review_does_not_import_evaluation() -> None:
    for path in Path("semantic_review").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "evaluation." not in source
        assert "from evaluation" not in source


def test_service_never_calls_human_resolve() -> None:
    for path in Path("semantic_review").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "resolve_case" not in source
        assert "build_canonical_entities" not in source


def test_openai_import_is_lazy() -> None:
    source = Path("semantic_review/providers/openai_provider.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_openai = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "openai":
                    top_level_openai = True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("openai"):
            top_level_openai = True
    assert top_level_openai is False
    assert "from openai import OpenAI" in source
