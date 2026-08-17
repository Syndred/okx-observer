"""Read-only view model helpers for the local four-layer opportunity dashboard."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from dashboard.redaction import redact_sensitive_text

BITGET_USDT_BASE = "https://www.bitget.com/zh-CN/futures/usdt"
SHANGHAI = ZoneInfo("Asia/Shanghai")
FOUR_LAYER_STAGES = (
    "entry_confirmed",
    "wait_first_pullback",
    "mature_15m_coil",
    "forming_15m_coil",
)
FORMAL_STAGES = FOUR_LAYER_STAGES[:2]
OBSERVATION_STAGES = FOUR_LAYER_STAGES[2:]
FEATURED_CAP = 9
FEATURED_MIN_RR = 2.0
MOVERS_CAP = 9
MOMENTUM_CAP = 9
MOMENTUM_LAUNCHED = frozenset(
    {
        "momentum_up",
        "momentum_down",
        "volume_spike_up",
        "volume_spike_down",
        "four_hour_up",
        "four_hour_down",
    }
)
MOMENTUM_WATCH = frozenset({"watch_up", "watch_down"})
MOMENTUM_LABELS = {
    "momentum_up": "连续上涨且放量",
    "momentum_down": "连续下跌且放量",
    "volume_spike_up": "首日放量上涨",
    "volume_spike_down": "首日放量下跌",
    "four_hour_up": "4H已启动上涨",
    "four_hour_down": "4H已启动下跌",
    "watch_up": "连涨、量能未放大",
    "watch_down": "连跌、量能未放大",
}
INVALID_REASON_MARKERS = (
    "failed",
    "expired",
    "invalid",
    "non_contiguous",
    "insufficient",
    "not_eligible",
    "ineligible",
)
REQUIRED_REPORT_COLUMNS = frozenset({"instrument", "role", "stage", "reason"})
STAGE_PROGRESS = {
    "entry_confirmed": 100,
    "wait_first_pullback": 78,
    "mature_15m_coil": 55,
    "forming_15m_coil": 35,
}
STAGE_LABELS = {
    "entry_confirmed": "回踩确认",
    "wait_first_pullback": "等待首次回踩",
    "mature_15m_coil": "密集成熟",
    "forming_15m_coil": "正在形成密集",
}
PLAYBOOK_LABELS = {
    "pullback_20": "回踩20",
    "coil_retest": "密集回测",
    "observation": "观察",
    "none": "无",
}
DIRECTION_LABELS = {
    "long": "做多",
    "short": "做空",
    "neutral": "方向待定",
}
FOUR_HOUR_CONTEXT_LABELS = {
    "mature_coil": "密集成熟",
    "forming_coil": "正在收拢",
    "trend_aligned": "高周期同向",
    "daily_trend_only": "仅日线有趋势",
    "unknown": "高周期不明确",
    "opposite": "高周期反向",
}
FIFTEEN_MINUTE_STATE_LABELS = {
    "breakout_long": "向上突破",
    "breakout_short": "向下突破",
    "mature_coil": "密集成熟",
    "forming_coil": "正在收拢",
}
RISK_LABELS = {
    "expanded_risk": "均线已经发散",
    "higher_timeframe_unconfirmed": "高周期尚未确认",
    "daily_unknown": "日线方向不明确",
    "opposite": "高周期方向相反",
}
COUNTERTREND_SIGNAL_DETAILS = {
    "bottoming_observation": ("抄底观察", "15m逆势下探，等止跌确认"),
    "topping_observation": ("摸顶观察", "15m逆势冲高，等转弱确认"),
}
READINESS_LABELS = (
    (80, "接近执行"),
    (60, "重点观察"),
    (40, "等待条件"),
    (0, "风险偏高"),
)


def bitget_url(instrument: str) -> str:
    """Build the public Bitget USDT contract URL for an OKX instrument."""

    base = instrument.removesuffix("-USDT-SWAP")
    safe = "".join(char for char in base.upper() if char.isalnum())
    return f"{BITGET_USDT_BASE}/{safe}USDT"


def format_shanghai(value: object) -> str | None:
    """Format a timestamp as a timezone-independent Beijing local time."""

    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    parsed = (
        parsed.tz_localize("UTC")
        if parsed.tzinfo is None
        else parsed.tz_convert("UTC")
    )
    return parsed.tz_convert(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def load_dashboard(report_dir: Path) -> dict[str, object]:
    """Load the latest scan artifacts into stable, JSON-friendly view data."""

    report_dir = Path(report_dir)
    csv_path = report_dir / "latest.csv"
    manifest_path = report_dir / "run-manifest.json"
    if not csv_path.exists() or not manifest_path.exists():
        return _empty_dashboard("not_scanned")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_dashboard("report_unavailable", "invalid_manifest")
    if not isinstance(manifest, dict):
        return _empty_dashboard("report_unavailable", "invalid_manifest")

    try:
        frame = pd.read_csv(csv_path)
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
    ):
        return _empty_dashboard("report_unavailable", "invalid_csv")
    if frame.empty:
        return _empty_dashboard("report_unavailable", "empty_csv")
    if not REQUIRED_REPORT_COLUMNS.issubset(frame.columns):
        return _empty_dashboard("report_unavailable", "invalid_csv_schema")

    records = [_record_from_row(row) for _, row in frame.iterrows()]
    visible = [record for record in records if _is_visible_record(record)]
    ordered = sorted(visible, key=_sort_key)
    observations = [
        record for record in ordered if record.get("stage") in OBSERVATION_STAGES
    ]
    observations = observations[:_observation_cap(manifest)]
    visible_by_stage = {
        stage: [record for record in ordered if record.get("stage") == stage]
        for stage in FORMAL_STAGES
    }
    for stage in OBSERVATION_STAGES:
        visible_by_stage[stage] = [
            record for record in observations if record.get("stage") == stage
        ]
    errors = redact_sensitive_text(_json_value(manifest.get("errors")))
    if not isinstance(errors, (dict, list)):
        errors = {}
    error_count = len(errors)
    scanner_universe = _json_value(manifest.get("scannerUniverse"))
    if not isinstance(scanner_universe, dict):
        scanner_universe = {}
    scanner_exclusion_counts = _json_value(manifest.get("scannerExclusionCounts"))
    if not isinstance(scanner_exclusion_counts, dict):
        scanner_exclusion_counts = {}
    observation_counts = _json_value(manifest.get("observationCounts"))
    if not isinstance(observation_counts, dict):
        observation_counts = {}
    return {
        "summary": {
            "status": "ready",
            "retrieved_at_shanghai": format_shanghai(manifest.get("retrievedAt")),
            "live_usdt_swaps": _json_value(manifest.get("liveUsdtSwaps")),
            "eligible_trade_contracts": _json_value(
                manifest.get("eligibleTradeContracts")
            ),
            "visible_opportunities": len(visible_by_stage["entry_confirmed"])
            + len(visible_by_stage["wait_first_pullback"])
            + len(observations),
            "error_count": error_count,
        },
        "featured": _featured_records(visible_by_stage),
        "movers": _movers_board(records),
        "momentum": _momentum_board(records),
        "stages": visible_by_stage,
        "diagnostics": {
            "error_count": error_count,
            "errors": errors,
            "claim_boundary": _json_value(manifest.get("claimBoundary")),
            "scanner_universe": scanner_universe,
            "scanner_exclusion_counts": scanner_exclusion_counts,
            "observation_counts": observation_counts,
        },
    }


def _empty_dashboard(
    status: str,
    reason: str | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {"error_count": 0, "errors": {}}
    result: dict[str, object] = {
        "summary": {
            "status": status,
            "retrieved_at_shanghai": None,
            "live_usdt_swaps": None,
            "eligible_trade_contracts": None,
            "visible_opportunities": 0,
            "error_count": 0,
        },
        "featured": [],
        "movers": {"gainers": [], "losers": []},
        "momentum": {"launched": [], "watch": []},
        "stages": {stage: [] for stage in FOUR_LAYER_STAGES},
        "diagnostics": diagnostics,
    }
    if reason is not None:
        diagnostics["reason"] = reason
    return result


def _record_from_row(row: pd.Series) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in row.items():
        if key == "bitget_url":
            continue
        if key.endswith("_at") or key.endswith("_window_end"):
            record[key] = format_shanghai(value)
        else:
            record[key] = _json_value(value)
    instrument = record.get("instrument")
    raw_bitget_available = record.get("bitget_available")
    bitget_available = raw_bitget_available is True or (
        isinstance(raw_bitget_available, str)
        and raw_bitget_available.strip().lower() == "true"
    )
    if bitget_available and isinstance(instrument, str) and instrument.strip():
        record["bitget_url"] = bitget_url(instrument)
    else:
        record["bitget_url"] = None
    # ``current_close`` is the latest completed 15m close produced by the
    # scanner. Keep the source field for backwards compatibility, while
    # exposing an explicit UI field so it cannot be confused with a stop or
    # zone boundary.
    record["latest_price"] = record.get("current_close")
    record["change_24h_pct"] = _optional_pct(record.get("change_24h_pct"))
    record["stage_label"] = _mapped_label(
        record.get("stage"), STAGE_LABELS, "状态待确认"
    )
    record["direction_label"] = _mapped_label(
        record.get("direction"), DIRECTION_LABELS, "方向待定"
    )
    record["four_hour_context_label"] = _mapped_label(
        record.get("four_hour_context") or record.get("four_hour_state"),
        FOUR_HOUR_CONTEXT_LABELS,
        "高周期不明确",
    )
    record["fifteen_minute_state_label"] = _mapped_label(
        record.get("fifteen_minute_state"),
        FIFTEEN_MINUTE_STATE_LABELS,
        "状态待确认",
    )
    record["daily_bias_label"] = _mapped_label(
        record.get("daily_bias") or record.get("daily_direction"),
        DIRECTION_LABELS,
        "方向待定",
    )
    record["playbook"] = str(record.get("playbook") or "none").strip().lower() or "none"
    record["playbook_label"] = _mapped_label(
        record["playbook"], PLAYBOOK_LABELS, "无"
    )
    record["planned_rr"] = _optional_rr(record.get("planned_rr"))
    record["odds_ok"] = _as_bool(record.get("odds_ok"))
    record["four_hour_expanded"] = _as_bool(record.get("four_hour_expanded"))
    context_values = {
        str(record.get(field) or "").strip().lower()
        for field in ("four_hour_context", "four_hour_state")
    }
    risk_codes = _risk_label_codes(record.get("risk_labels"))
    # The 4H context cell already says "高周期反向". Do not repeat the same
    # fact as a badge; retain other independent risks such as expanded risk.
    if "opposite" in context_values:
        risk_codes = [code for code in risk_codes if code != "opposite"]
    risk_codes = list(dict.fromkeys(risk_codes))
    record["risk_labels_display"] = [
        RISK_LABELS.get(code, "其他风险") for code in risk_codes
    ]
    signal_code, signal_label, signal_detail = _countertrend_signal(record)
    record["countertrend_signal"] = signal_code
    record["countertrend_signal_label"] = signal_label
    record["countertrend_signal_detail"] = signal_detail
    if record["playbook"] == "none":
        record["playbook"] = _infer_playbook(record)
        record["playbook_label"] = _mapped_label(
            record["playbook"], PLAYBOOK_LABELS, "无"
        )
    if (
        record["playbook"] == "pullback_20"
        and record["planned_rr"] is not None
        and record["planned_rr"] >= FEATURED_MIN_RR
        and not record.get("countertrend_signal")
    ):
        record["odds_ok"] = True
    record["entry_readiness"] = _entry_readiness(record)
    record["entry_readiness_label"] = _readiness_label(
        record["entry_readiness"]
    )
    return record


def _countertrend_signal(
    record: dict[str, object],
) -> tuple[str | None, str | None, str | None]:
    """Derive a cautious 15m-vs-4H countertrend observation.

    The scanner's explicit 4H direction and latest 15m close relation are the
    only inputs. Daily direction, the card's execution direction, and
    unknown/inside-band values are intentionally not used as fallbacks.
    """

    four_hour_direction = str(
        record.get("four_hour_context_direction") or ""
    ).strip().lower()
    relation = str(record.get("current_close_vs_six_lines") or "").strip().lower()
    if four_hour_direction == "long" and relation == "below_all":
        code = "bottoming_observation"
    elif four_hour_direction == "short" and relation == "above_all":
        code = "topping_observation"
    else:
        return None, None, None
    label, detail = COUNTERTREND_SIGNAL_DETAILS[code]
    return code, label, detail


def _json_value(value: object) -> object:
    """Convert pandas missing/scalar values into values accepted by JSON."""

    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            normalized = item()
            if normalized is not value:
                return _json_value(normalized)
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _momentum_board(records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    """Keep consecutive-move / volume-expansion names on the same page."""

    launched: list[dict[str, object]] = []
    watch: list[dict[str, object]] = []
    for record in records:
        if record.get("role") != "trade":
            continue
        scanner_eligible = record.get("scanner_eligible")
        if scanner_eligible is not None and not _as_bool(scanner_eligible):
            continue
        status = str(record.get("momentum_status") or "").strip().lower()
        if status not in MOMENTUM_LAUNCHED and status not in MOMENTUM_WATCH:
            continue
        card = {
            "instrument": record.get("instrument"),
            "latest_price": record.get("latest_price"),
            "change_24h_pct": _optional_pct(record.get("change_24h_pct")),
            "bitget_url": record.get("bitget_url"),
            "status": status,
            "status_label": MOMENTUM_LABELS.get(status, status),
            "direction": record.get("direction") or "none",
            "daily_streak": _json_value(record.get("daily_streak")),
            "four_hour_streak": _json_value(record.get("four_hour_streak")),
            "volume_ratio": _json_value(record.get("volume_ratio")),
            "score": _numeric(record.get("momentum_score")),
        }
        if status in MOMENTUM_LAUNCHED:
            launched.append(card)
        else:
            watch.append(card)
    launched.sort(
        key=lambda item: (-_numeric(item.get("score")), str(item.get("instrument") or ""))
    )
    watch.sort(
        key=lambda item: (-_numeric(item.get("score")), str(item.get("instrument") or ""))
    )
    return {
        "launched": launched[:MOMENTUM_CAP],
        "watch": watch[:MOMENTUM_CAP],
    }


def _movers_board(records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    """Rank eligible contracts by completed 24h percent change."""

    eligible = [record for record in records if _is_mover_record(record)]
    gainers = sorted(
        [record for record in eligible if _numeric(record.get("change_24h_pct")) > 0],
        key=lambda record: (-_numeric(record.get("change_24h_pct")), str(record.get("instrument") or "")),
    )[:MOVERS_CAP]
    losers = sorted(
        [record for record in eligible if _numeric(record.get("change_24h_pct")) < 0],
        key=lambda record: (_numeric(record.get("change_24h_pct")), str(record.get("instrument") or "")),
    )[:MOVERS_CAP]
    return {
        "gainers": [_mover_card(record) for record in gainers],
        "losers": [_mover_card(record) for record in losers],
    }


def _mover_card(record: dict[str, object]) -> dict[str, object]:
    change = _optional_pct(record.get("change_24h_pct"))
    return {
        "instrument": record.get("instrument"),
        "latest_price": record.get("latest_price"),
        "change_24h_pct": change,
        "bitget_url": record.get("bitget_url"),
    }


def _is_mover_record(record: dict[str, object]) -> bool:
    if record.get("role") != "trade":
        return False
    scanner_eligible = record.get("scanner_eligible")
    if scanner_eligible is not None and not _as_bool(scanner_eligible):
        return False
    return _optional_pct(record.get("change_24h_pct")) is not None


def _featured_records(
    visible_by_stage: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Pick the front-page watch list from all four layers.

    High-odds pullback-20 setups stay first. Other visible cards fill the
    remaining slots by readiness. 4H-opposite and low-readiness names are
    kept but sorted later, so the block does not disappear on a reverse day.
    """

    pool = [
        record
        for stage in FOUR_LAYER_STAGES
        for record in (visible_by_stage.get(stage) or [])
    ]

    def sort_key(record: dict[str, object]) -> tuple[object, ...]:
        return (
            0 if _is_high_odds_setup(record) else 1,
            1 if _is_opposite_context(record) else 0,
            1 if _numeric(record.get("entry_readiness")) < 40 else 0,
            *_featured_sort_key(record),
        )

    seen: set[str] = set()
    ordered: list[dict[str, object]] = []
    for record in sorted(pool, key=sort_key):
        instrument = str(record.get("instrument") or "")
        if not instrument or instrument in seen:
            continue
        seen.add(instrument)
        ordered.append(record)
        if len(ordered) >= FEATURED_CAP:
            break
    return ordered


def _is_opposite_context(record: dict[str, object]) -> bool:
    context = str(
        record.get("four_hour_context") or record.get("four_hour_state") or ""
    ).strip().lower()
    return context == "opposite" or "opposite" in set(
        _risk_label_codes(record.get("risk_labels"))
    )


def _is_high_odds_setup(record: dict[str, object]) -> bool:
    if record.get("countertrend_signal"):
        return False
    if str(record.get("playbook") or "") != "pullback_20":
        return False
    if not _as_bool(record.get("odds_ok")):
        return False
    if _numeric(record.get("planned_rr")) < FEATURED_MIN_RR:
        return False
    context = str(
        record.get("four_hour_context") or record.get("four_hour_state") or ""
    ).strip().lower()
    if context == "opposite":
        return False
    risks = set(_risk_label_codes(record.get("risk_labels")))
    if "opposite" in risks:
        return False
    return str(record.get("stage") or "") in FORMAL_STAGES


def _infer_playbook(record: dict[str, object]) -> str:
    stage = str(record.get("stage") or "").strip().lower()
    direction = str(record.get("direction") or "").strip().lower()
    context = str(
        record.get("four_hour_context") or record.get("four_hour_state") or ""
    ).strip().lower()
    risks = set(_risk_label_codes(record.get("risk_labels")))
    if record.get("countertrend_signal") or context == "opposite" or "opposite" in risks:
        if stage in {"entry_confirmed", "wait_first_pullback", "mature_15m_coil"}:
            return "coil_retest"
        return "observation"
    htf = str(record.get("four_hour_context_direction") or "").strip().lower()
    daily = str(
        record.get("daily_bias") or record.get("daily_direction") or ""
    ).strip().lower()
    expanded = context == "trend_aligned" or "expanded_risk" in risks
    aligned = direction in {"long", "short"} and (
        htf == direction or daily == direction or context == "trend_aligned"
    )
    if (
        stage in {"entry_confirmed", "wait_first_pullback"}
        and expanded
        and aligned
        and context not in {"mature_coil", "forming_coil"}
    ):
        return "pullback_20"
    if stage in {"entry_confirmed", "wait_first_pullback", "mature_15m_coil"}:
        return "coil_retest"
    if stage == "forming_15m_coil":
        return "observation"
    return "none"


def _featured_sort_key(
    record: dict[str, object],
) -> tuple[float, float, int, float, float, float, float, str]:
    readiness = _numeric(record.get("entry_readiness"))
    stage_rank, fifteen_minute_score, four_hour_score, daily_alignment, freshness, instrument = (
        _sort_key(record)
    )
    return (
        -_numeric(record.get("planned_rr")),
        -readiness,
        stage_rank,
        fifteen_minute_score,
        four_hour_score,
        daily_alignment,
        freshness,
        instrument,
    )


def _sort_key(record: dict[str, object]) -> tuple[int, float, float, float, float, str]:
    stage_order = {stage: index for index, stage in enumerate(FOUR_LAYER_STAGES)}
    stage = str(record.get("stage") or "")
    stage_rank = (
        int(_numeric(record["stage_priority"]))
        if "stage_priority" in record and record.get("stage_priority") is not None
        else stage_order.get(stage, len(FOUR_LAYER_STAGES))
    )
    fifteen_minute_score = _numeric(
        record.get(
            "fifteen_minute_state_quality",
            record.get("15m_state_quality", record.get("fifteen_minute_coil_score")),
        )
    )
    four_hour_score = _numeric(
        record.get("four_hour_context_score", record.get("four_hour_coil_score"))
    )
    daily_alignment = (
        _numeric(record["daily_alignment_bonus"])
        if "daily_alignment_bonus" in record
        and record.get("daily_alignment_bonus") is not None
        else _daily_alignment(record)
    )
    freshness = (
        _numeric(record["setup_freshness"])
        if "setup_freshness" in record and record.get("setup_freshness") is not None
        else _freshness(record)
    )
    instrument = str(record.get("instrument") or "")
    return (
        stage_rank,
        -fifteen_minute_score,
        -four_hour_score,
        -daily_alignment,
        -freshness,
        instrument,
    )


def _is_visible_record(record: dict[str, object]) -> bool:
    """Apply dashboard visibility gates without changing scanner diagnostics."""

    if record.get("role") != "trade":
        return False
    stage = record.get("stage")
    if stage not in FOUR_LAYER_STAGES:
        return False
    scanner_eligible = record.get("scanner_eligible")
    if scanner_eligible is not None and not _as_bool(scanner_eligible):
        return False
    reason = str(record.get("reason") or "").strip().lower()
    if any(marker in reason for marker in INVALID_REASON_MARKERS):
        return False
    if _fifteen_minute_score(record) <= 0:
        return False
    return True


def _observation_cap(manifest: dict[str, object]) -> int:
    counts = manifest.get("observationCounts")
    if isinstance(counts, dict):
        try:
            value = int(float(counts.get("cap", 20)))
            return max(0, value)
        except (TypeError, ValueError, OverflowError):
            pass
    return 20


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _daily_alignment(record: dict[str, object]) -> float:
    direction = str(record.get("direction") or "").lower()
    daily = str(record.get("daily_bias") or record.get("daily_direction") or "").lower()
    if direction in {"long", "short"} and direction == daily:
        return 1.0
    if direction in {"long", "short"} and daily in {"long", "short"}:
        return -1.0
    return 0.0


def _freshness(record: dict[str, object]) -> float:
    for field in (
        "next_executable_at",
        "first_pullback_at",
        "fifteen_minute_breakout_at",
        "four_hour_breakout_at",
        "fifteen_minute_window_end",
    ):
        value = record.get(field)
        if value:
            try:
                return pd.Timestamp(value).value / 1_000_000_000
            except (TypeError, ValueError, OverflowError):
                continue
    return float("-inf")


def _optional_pct(value: object) -> float | None:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if value is None or (isinstance(missing, bool) and missing):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 4)


def _optional_rr(value: object) -> float | None:
    number = _numeric(value)
    if number <= 0:
        return None
    return round(number, 2)


def _numeric(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def _bounded_score(value: object) -> float:
    number = _numeric(value)
    if not math.isfinite(number):
        return 0.0
    return min(100.0, max(0.0, number))


def _fifteen_minute_score(record: dict[str, object]) -> float:
    for field in (
        "fifteen_minute_state_quality",
        "15m_state_quality",
        "fifteen_minute_coil_score",
    ):
        value = record.get(field)
        if value is not None:
            return _bounded_score(value)
    return 0.0


def _four_hour_score(record: dict[str, object]) -> float:
    for field in ("four_hour_context_score", "four_hour_coil_score"):
        value = record.get(field)
        if value is not None:
            return _bounded_score(value)
    return 0.0


def _entry_readiness(record: dict[str, object]) -> int:
    stage = str(record.get("stage") or "").strip().lower()
    base = (
        STAGE_PROGRESS.get(stage, 0) * 0.55
        + _fifteen_minute_score(record) * 0.35
        + _four_hour_score(record) * 0.10
    )
    risk_codes = set(_risk_label_codes(record.get("risk_labels")))
    context = str(
        record.get("four_hour_context") or record.get("four_hour_state") or ""
    ).strip().lower()
    daily = str(
        record.get("daily_bias") or record.get("daily_direction") or ""
    ).strip().lower()
    if context == "opposite":
        risk_codes.add("opposite")
    if context in {"daily_trend_only", "unknown"}:
        risk_codes.add("higher_timeframe_unconfirmed")
    if daily == "unknown":
        risk_codes.add("daily_unknown")
    deductions = sum(
        deduction
        for code, deduction in {
            "opposite": 25,
            "expanded_risk": 0 if str(record.get("playbook") or "") == "pullback_20" else 10,
            "higher_timeframe_unconfirmed": 8,
            "daily_unknown": 5,
        }.items()
        if code in risk_codes
    )
    return int(min(100, max(0, round(base - deductions))))


def _readiness_label(value: object) -> str:
    score = int(min(100, max(0, _numeric(value))))
    for threshold, label in READINESS_LABELS:
        if score >= threshold:
            return label
    return "风险偏高"


def _mapped_label(
    value: object,
    mapping: dict[str, str],
    fallback: str,
) -> str:
    code = str(value or "").strip().lower()
    return mapping.get(code, fallback)


def _risk_label_codes(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        values = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            values = parsed
        else:
            values = text.strip("[]").split(",")
    else:
        return []
    return [
        str(item).strip().strip("'\"").lower()
        for item in values
        if str(item).strip().strip("'\"")
    ]
