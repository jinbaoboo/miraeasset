"""Auditable Decimal calculations; missing values are never coerced to zero."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List


def _decimal(value: Any) -> Decimal:
    if value is None:
        raise ValueError("missing numeric input")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"invalid numeric input: {value!r}") from error


def calculate(operation: str, values: Iterable[Any]) -> Dict[str, Any]:
    numbers: List[Decimal] = [_decimal(value) for value in values]
    if not numbers:
        raise ValueError("at least one value is required")
    if operation == "sum": result = sum(numbers, Decimal(0)); formula = " + ".join(map(str, numbers))
    elif operation == "difference" and len(numbers) == 2: result = numbers[0] - numbers[1]; formula = f"{numbers[0]} - {numbers[1]}"
    elif operation == "growth_rate" and len(numbers) == 2:
        if numbers[1] == 0: raise ValueError("growth rate denominator is zero")
        result = (numbers[0] - numbers[1]) / abs(numbers[1]) * Decimal(100); formula = f"({numbers[0]} - {numbers[1]}) / abs({numbers[1]}) * 100"
    elif operation == "ratio" and len(numbers) == 2:
        if numbers[1] == 0: raise ValueError("ratio denominator is zero")
        result = numbers[0] / numbers[1] * Decimal(100); formula = f"{numbers[0]} / {numbers[1]} * 100"
    else: raise ValueError(f"unsupported operation or arity: {operation}/{len(numbers)}")
    return {"operation": operation, "inputs": [str(v) for v in numbers], "formula": formula,
            "result": str(result), "result_float": float(result)}
