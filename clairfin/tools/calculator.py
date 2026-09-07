from __future__ import annotations

from decimal import Decimal


def _dec(value: float | str) -> Decimal:
    return Decimal(str(value))


def yoy_growth(current: float, previous: float) -> Decimal:
    current_d, previous_d = _dec(current), _dec(previous)
    if previous_d == 0:
        raise ZeroDivisionError("previous value is zero — YoY growth is undefined")
    return (current_d - previous_d) / previous_d


def ratio(numerator: float, denominator: float) -> Decimal:
    numerator_d, denominator_d = _dec(numerator), _dec(denominator)
    if denominator_d == 0:
        raise ZeroDivisionError("denominator is zero")
    return numerator_d / denominator_d


def percentage_point_diff(current: float, previous: float) -> Decimal:
    """Plain subtraction for two values that are already percentages/rates (e.g. an inflation
    rate or GDP growth rate changing from 10.66% to 10.70%). Distinct from `yoy_growth`, which
    computes the *relative* change of a value — applying `yoy_growth` to two already-percentage
    figures answers a different question than "by how many percentage points did X change"."""
    return _dec(current) - _dec(previous)


def within_tolerance(observed: float, expected: float, relative_tolerance: float) -> bool:
    expected_d = _dec(expected)
    if expected_d == 0:
        return _dec(observed) == 0
    return abs(_dec(observed) - expected_d) / abs(expected_d) <= _dec(relative_tolerance)
