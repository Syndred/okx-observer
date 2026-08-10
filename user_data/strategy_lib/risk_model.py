from __future__ import annotations


def collateral_for_risk(
    *,
    equity: float,
    risk_pct: float,
    stop_distance_ratio: float,
    leverage: float,
    max_collateral: float,
) -> float:
    """Return collateral whose stop loss equals the requested equity risk."""
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct must be in (0, 1]")
    if stop_distance_ratio <= 0:
        raise ValueError("stop_distance_ratio must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if max_collateral < 0:
        raise ValueError("max_collateral cannot be negative")

    requested = (equity * risk_pct) / (stop_distance_ratio * leverage)
    return min(requested, max_collateral)
