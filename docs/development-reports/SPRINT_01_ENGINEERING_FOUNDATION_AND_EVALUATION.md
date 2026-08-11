# Sprint 01 — Engineering Foundation and Evaluation

## Objective
Sprint 01 establishes the engineering foundation and the first executable evaluation infrastructure for the AI Data Quality Platform.

The primary goal is to ensure that future data-quality and entity-resolution components can be measured objectively before product-facing layers are introduced.

## Scope
Sprint 01 includes:
- Python project configuration
- isolated virtual environment
- Ruff static analysis
- Pytest test infrastructure
- centralized evaluation configuration
- reusable classification metrics
- configurable hard-gate evaluation
- evaluation CLI
- JSON and Markdown reporting
- deterministic exit-code behavior
- GitHub Actions continuous integration

## Evaluation Configuration
Initial hard gates are defined centrally in:

```text

configs/evaluation.yaml

```

The initial evaluation configuration includes gates for:
- AUTO\_MERGE precision
- false merge rate
- candidate recall
- schema mapping accuracy
- normalization accuracy
- review routing recall

Gate direction and threshold are explicit through `gte` and `lte` operators.

## Classification Metrics
The initial reusable metric layer supports:
- precision
- recall
- F1
- safe zero-denominator behavior

Metric calculations are independently unit-tested.

## Hard Gates
Evaluation results can be compared with configured thresholds.

Each gate produces:
- metric name
- actual value
- threshold
- comparison operator
- PASS / FAIL result

The overall evaluation fails when any required hard gate fails.

## Reporting
The evaluation harness produces:

```text

evaluation/reports/latest/report.json

evaluation/reports/latest/report.md

```

Generated reports are runtime artifacts and are excluded from version control.

## CLI Contract
The evaluation harness can be executed with:

```bash

python -m evaluation.run

```

A custom configuration can be supplied with:

```bash

python -m evaluation.run --config <path>

```

Process exit codes:
```text

0 = all hard gates passed

1 = one or more hard gates failed

2 = evaluation infrastructure error

```

This contract allows the same evaluation runner to be used locally and in continuous integration.

## Quality Validation
Sprint 01 completes with:
- Ruff static analysis passing
- 18 automated tests passing
- evaluation CLI executing successfully
- hard-gate evaluation returning PASS
- evaluation exit code returning 0
- JSON and Markdown report generation working

## Architectural Decisions

### Evaluation First
The project does not begin with FastAPI, React, n8n, or CRM integrations.

The reliability of the core data-processing system must be measurable before productization begins.

### Configuration-Driven Gates
Hard-gate thresholds are stored outside application logic so that evaluation policy remains explicit and version-controlled.

### Generated Reports Are Not Source Artifacts
Evaluation reports are reproducible runtime outputs and are therefore excluded from Git.

### AI Is Not Yet Part of the Baseline
Sprint 01 contains no LLM dependency.

Later AI components will be evaluated against deterministic baselines rather than introduced as the default solution.

## Next Sprint
Sprint 02 will introduce:
- versioned golden datasets
- reproducible clean customer data generation
- controlled corruption generation
- ground-truth labels
- hard positives
- hard negatives
- malformed input fixtures
- dataset manifests and reproducibility metadata
