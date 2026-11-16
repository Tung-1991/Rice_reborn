"""Audited pure calculations ported from MayinTien; no exchange/session dependencies."""
from dataclasses import dataclass
from typing import Any, Iterable

def closes(rows: Iterable[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows or []:
        try:
            value = float(row.get("close", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return values

def ema(values: Iterable[float], period: int) -> list[float]:
    source = [float(value) for value in values]
    if not source:
        return []
    period = max(1, int(period or 1))
    alpha = 2.0 / (period + 1.0)
    output = [source[0]]
    for value in source[1:]:
        output.append((value * alpha) + (output[-1] * (1.0 - alpha)))
    return output

def sma(values: Iterable[float], period: int) -> list[float | None]:
    source = [float(value) for value in values]
    period = max(1, int(period or 1))
    output: list[float | None] = []
    running = 0.0
    for index, value in enumerate(source):
        running += value
        if index >= period:
            running -= source[index - period]
        output.append(running / period if index + 1 >= period else None)
    return output

def rsi(values: Iterable[float], period: int = 14) -> list[float | None]:
    source = [float(value) for value in values]
    if not source:
        return []
    period = max(1, int(period or 14))
    output: list[float | None] = [None] * len(source)
    if len(source) <= period:
        return output
    gains = [max(0.0, source[i] - source[i - 1]) for i in range(1, len(source))]
    losses = [max(0.0, source[i - 1] - source[i]) for i in range(1, len(source))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + gain / loss))

    output[period] = value(avg_gain, avg_loss)
    for index in range(period + 1, len(source)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index - 1]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index - 1]) / period
        output[index] = value(avg_gain, avg_loss)
    return output

def pivot_points(rows: list[dict[str, Any]], left: int = 3, right: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    left = max(1, int(left or 3))
    right = max(1, int(right or 3))
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for index in range(left, len(rows) - right):
        try:
            high = float(rows[index].get("high", 0.0) or 0.0)
            low = float(rows[index].get("low", 0.0) or 0.0)
            left_rows = rows[index - left:index]
            right_rows = rows[index + 1:index + right + 1]
            neighbor_highs = [float(row.get("high", 0.0) or 0.0) for row in left_rows + right_rows]
            neighbor_lows = [float(row.get("low", 0.0) or 0.0) for row in left_rows + right_rows]
        except (AttributeError, TypeError, ValueError):
            continue
        if high > 0 and all(high > value for value in neighbor_highs):
            highs.append((index, high))
        if low > 0 and all(low < value for value in neighbor_lows if value > 0):
            lows.append((index, low))
    return highs, lows

@dataclass
class ProtectLevel:
    """One trader-facing PROTECT level shared by live, PAPER and backtest."""

    mfe_pct: float
    peak_price: float
    activation_mfe_pct: float
    effective_trail_pct: float
    trigger_price: float
    trigger_profit_pct: float
    active: bool
    state: str

def protect_level(
    entry_price: float,
    peak_profit_pct: float,
    arm_pct: float,
    trailing_gap_pct: float,
    *,
    dynamic_enabled: bool = False,
    sl_price: float = 0.0,
    atr_pct: float = 0.0,
    atr_multiplier: float = 0.6,
    atr_activation_multiplier: float | None = None,
    atr_activation_enabled: bool = True,
    atr_trail_enabled: bool = True,
    retention_pct: float = 0.0,
    retention_until_pct: float | None = None,
    retention_enabled: bool = True,
    retention_until_enabled: bool = True,
    previous_trigger_price: float = 0.0,
) -> ProtectLevel:
    """Calculate PROTECT from running MFE without using future data.

    At/above ARM, TRAIL subtracts percentage points from profit on entry cost.
    Below ARM, each enabled floor contributes independently.  START can gate
    both floors; with START off they begin after the first observed gain.
    With the retention cutoff off, retention continues until ARM.
    ``None`` preserves the V2 contract by reusing the trail multiplier.  A
    level below the actual SL is informational only.  A previously active
    level never moves down when ATR changes on a later session.
    """
    entry = max(0.0, float(entry_price or 0.0))
    mfe = max(0.0, float(peak_profit_pct or 0.0))
    arm = max(0.0, float(arm_pct or 0.0))
    trail = max(0.0, float(trailing_gap_pct or 0.0))
    armed = mfe + 1e-9 >= arm
    atr_distance = max(0.0, float(atr_pct or 0.0)) * max(
        0.0, float(atr_multiplier or 0.0),
    )
    activation_multiplier = (
        atr_multiplier
        if atr_activation_multiplier is None
        else atr_activation_multiplier
    )
    activation_distance = (
        max(0.0, float(atr_pct or 0.0)) * max(0.0, float(activation_multiplier or 0.0))
        if atr_activation_enabled else 0.0
    )
    atr_floor_enabled = bool(dynamic_enabled and atr_trail_enabled and float(atr_pct or 0.0) > 0)
    retention_floor_enabled = bool(
        dynamic_enabled and retention_enabled and float(retention_pct or 0.0) > 0
    )
    any_dynamic_floor = atr_floor_enabled or retention_floor_enabled
    effective = trail if armed or not dynamic_enabled else atr_distance if atr_floor_enabled else 0.0
    peak_price = entry * (1.0 + mfe / 100.0) if entry > 0 else 0.0
    calculated = (
        (entry * (1.0 + (mfe - trail) / 100.0) if armed else peak_price * (1.0 - effective / 100.0))
        if peak_price > 0 and (armed or atr_floor_enabled) else 0.0
    )
    retention = (
        min(100.0, max(0.0, float(retention_pct or 0.0)))
        if retention_floor_enabled else 0.0
    )
    retention_until = (
        arm
        if not retention_until_enabled or retention_until_pct is None
        else min(arm, max(0.0, float(retention_until_pct or 0.0)))
    )
    if (
        retention_floor_enabled and not armed and retention > 0 and entry > 0
        and mfe + 1e-9 < retention_until
    ):
        retained_profit_pct = mfe * retention / 100.0
        calculated = max(
            calculated,
            entry * (1.0 + retained_profit_pct / 100.0),
        )
    previous = max(0.0, float(previous_trigger_price or 0.0))
    sl_floor = max(0.0, float(sl_price or 0.0))
    significant = bool(
        armed
        or (
            any_dynamic_floor and (
                (atr_activation_enabled and activation_distance > 0
                 and mfe + 1e-9 >= activation_distance)
                or (not atr_activation_enabled and mfe > 0)
            )
        )
        or (any_dynamic_floor and previous > sl_floor + 1e-9)
    )
    trigger = max(calculated, previous) if significant else (
        previous if any_dynamic_floor else 0.0
    )
    trigger_profit = ((trigger / entry) - 1.0) * 100.0 if entry > 0 else 0.0
    active = bool(
        trigger > 0
        and significant
        and trigger > sl_floor + 1e-9
    )
    state = "ARM" if active and armed else "DYN" if active else "WAIT"
    return ProtectLevel(
        mfe_pct=mfe,
        peak_price=peak_price,
        activation_mfe_pct=activation_distance,
        effective_trail_pct=effective,
        trigger_price=trigger,
        trigger_profit_pct=trigger_profit,
        active=active,
        state=state,
    )
