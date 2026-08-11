from collections.abc import Mapping
from dataclasses import dataclass

SUPPORTED_OPERATORS = {"gte", "lte"}


@dataclass(frozen=True)
class HardGateResult:
    name: str
    actual: float
    threshold: float
    operator: str
    passed: bool


def evaluate_gate(
    *,
    name: str,
    actual: float,
    threshold: float,
    operator: str,
) -> HardGateResult:
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"Unsupported hard-gate operator: {operator}")

    if operator == "gte":
        passed = actual >= threshold
    else:
        passed = actual <= threshold

    return HardGateResult(
        name=name,
        actual=actual,
        threshold=threshold,
        operator=operator,
        passed=passed,
    )


def evaluate_hard_gates(
    *,
    metrics: Mapping[str, float],
    gate_config: Mapping[str, Mapping[str, object]],
) -> list[HardGateResult]:
    results: list[HardGateResult] = []

    for gate_name, gate_definition in gate_config.items():
        if gate_name not in metrics:
            raise KeyError(f"Metric required by hard gate is missing: {gate_name}")

        operator = str(gate_definition["operator"])
        threshold = float(gate_definition["threshold"])
        actual = float(metrics[gate_name])

        results.append(
            evaluate_gate(
                name=gate_name,
                actual=actual,
                threshold=threshold,
                operator=operator,
            )
        )

    return results


def all_hard_gates_pass(results: list[HardGateResult]) -> bool:
    return all(result.passed for result in results)
