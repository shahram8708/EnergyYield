import logging
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func

from database import db
from models import DailySummary, Device, DeviceSettings, MovementLog, SlotStatistic, Telemetry


ANGLE_ROUND = 0  # round angle to nearest integer degree for aggregation


def _angle_bucket():
    # SQLite does not support date_trunc; we only need rounding
    return func.round(Telemetry.angle_deg, ANGLE_ROUND)


def ensure_settings(device_id: str) -> DeviceSettings:
    settings = DeviceSettings.query.filter_by(device_id=device_id).first()
    if settings:
        return settings
    settings = DeviceSettings(
        device_id=device_id,
        mode="auto",
        min_net_gain_wh=0.0,
        max_moves_per_hour=12,
        motor_power_w=50.0,
        hold_power_w=2.0,
    )
    db.session.add(settings)
    db.session.commit()
    return settings


def generate_slot_statistics(days: int = 7) -> None:
    cutoff = datetime.utcnow() - timedelta(days=days)
    angle_bin = _angle_bucket().label("angle_bin")
    rows = (
        db.session.query(
            Telemetry.device_id,
            Telemetry.slot,
            angle_bin,
            func.count(Telemetry.id),
            func.avg(Telemetry.p_w),
            func.avg(Telemetry.p_w * Telemetry.p_w),
        )
        .filter(Telemetry.ts >= cutoff)
        .group_by(Telemetry.device_id, Telemetry.slot, angle_bin)
        .all()
    )
    now = datetime.utcnow()
    for device_id, slot, angle, sample_count, avg_power, avg_power_sq in rows:
        if angle is None:
            continue
        mean_power = float(avg_power or 0.0)
        variance = max(float(avg_power_sq or 0.0) - mean_power * mean_power, 0.0)
        std_power = math.sqrt(variance)
        stat = SlotStatistic.query.filter_by(device_id=device_id, slot=int(slot), angle_deg=float(angle)).first()
        if not stat:
            stat = SlotStatistic(device_id=device_id, slot=int(slot), angle_deg=float(angle))
            db.session.add(stat)
        stat.sample_count = int(sample_count or 0)
        stat.avg_power = mean_power
        stat.std_power = float(std_power)
        stat.last_updated = now
    db.session.commit()


def best_angle_per_slot(device_id: str) -> List[Dict[str, float]]:
    stats = SlotStatistic.query.filter_by(device_id=device_id).all()
    by_slot: Dict[int, List[SlotStatistic]] = {}
    for s in stats:
        by_slot.setdefault(s.slot, []).append(s)

    results: List[Dict[str, float]] = []
    for slot, items in by_slot.items():
        if not items:
            continue
        total_samples = sum(i.sample_count for i in items) or 1
        best = max(items, key=lambda i: i.avg_power)
        confidence = (best.sample_count / total_samples) if total_samples else 0.0
        results.append(
            {
                "slot": slot,
                "best_angle": float(best.angle_deg),
                "confidence": round(confidence, 3),
                "avg_power": float(best.avg_power),
            }
        )
    return sorted(results, key=lambda x: x["slot"])


def movement_efficiency(device_id: str, hours: int = 6) -> Dict[str, float]:
    since = datetime.utcnow() - timedelta(hours=hours)
    logs = (
        MovementLog.query.filter(MovementLog.device_id == device_id, MovementLog.ts >= since)
        .order_by(MovementLog.ts.asc())
        .all()
    )
    if not logs:
        return {
            "moves_per_hour": 0.0,
            "wasted_moves_percent": 0.0,
            "energy_gain_per_move": 0.0,
        }

    moves_per_hour = len(logs) / float(hours or 1)
    wasted = 0
    gains: List[float] = []

    for log in logs:
        before = (
            Telemetry.query.filter(
                Telemetry.device_id == device_id,
                Telemetry.ts <= log.ts,
                Telemetry.ts >= log.ts - timedelta(minutes=10),
            )
            .order_by(Telemetry.ts.desc())
            .first()
        )
        after = (
            Telemetry.query.filter(
                Telemetry.device_id == device_id,
                Telemetry.ts >= log.ts,
                Telemetry.ts <= log.ts + timedelta(minutes=10),
            )
            .order_by(Telemetry.ts.asc())
            .first()
        )
        if not before or not after:
            continue
        interval_hours = max((after.ts - before.ts).total_seconds() / 3600.0, 1 / 3600.0)
        energy_gain = (after.p_w - before.p_w) * interval_hours
        net_gain = energy_gain - float(log.energy_cost_wh or 0.0)
        gains.append(net_gain)
        if net_gain <= 0:
            wasted += 1

    energy_gain_per_move = sum(gains) / len(gains) if gains else 0.0
    wasted_pct = (wasted / len(logs) * 100.0) if logs else 0.0
    return {
        "moves_per_hour": round(moves_per_hour, 3),
        "wasted_moves_percent": round(wasted_pct, 2),
        "energy_gain_per_move": round(energy_gain_per_move, 3),
    }


def generate_daily_summaries(days: int = 7) -> None:
    start_day = date.today() - timedelta(days=days - 1)
    devices = Device.query.all()
    for device in devices:
        for i in range(days):
            day = start_day + timedelta(days=i)
            start_dt = datetime.combine(day, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)
            latest = (
                Telemetry.query.filter(
                    Telemetry.device_id == device.device_id,
                    Telemetry.ts >= start_dt,
                    Telemetry.ts < end_dt,
                )
                .order_by(Telemetry.ts.desc())
                .first()
            )
            if not latest:
                continue
            energy_wh = float(latest.e_wh_today or 0.0)
            move_count = int(latest.move_count_today or 0)
            efficiency_ratio = (energy_wh / move_count) if move_count > 0 else None

            summary = DailySummary.query.filter_by(device_id=device.device_id, date=day).first()
            if not summary:
                summary = DailySummary(device_id=device.device_id, date=day)
                db.session.add(summary)
            summary.energy_wh = energy_wh
            summary.move_count = move_count
            summary.efficiency_ratio = efficiency_ratio
    db.session.commit()


def heatmap_data(device_id: str) -> List[Dict[str, float]]:
    stats = (
        SlotStatistic.query.filter_by(device_id=device_id)
        .order_by(SlotStatistic.slot.asc(), SlotStatistic.angle_deg.asc())
        .all()
    )
    return [
        {"slot": s.slot, "angle": float(s.angle_deg), "avg_power": float(s.avg_power)}
        for s in stats
    ]


def _current_angle_power(stat_rows: List[SlotStatistic], angle: float) -> Optional[float]:
    for row in stat_rows:
        if abs(row.angle_deg - angle) <= 0.5:
            return float(row.avg_power)
    return None


def _estimate_move_duration(device_id: str) -> float:
    last_logs = (
        MovementLog.query.filter_by(device_id=device_id)
        .order_by(MovementLog.ts.desc())
        .limit(10)
        .all()
    )
    durations = [log.move_duration_sec for log in last_logs if log.move_duration_sec]
    if not durations:
        return 0.0
    return float(sum(durations) / len(durations))


def _moves_last_hour(device_id: str) -> int:
    since = datetime.utcnow() - timedelta(hours=1)
    return (
        db.session.query(func.count(MovementLog.id))
        .filter(MovementLog.device_id == device_id, MovementLog.ts >= since)
        .scalar()
        or 0
    )


def net_gain_projection(device_id: str) -> Optional[Dict[str, float]]:
    latest = (
        Telemetry.query.filter_by(device_id=device_id)
        .order_by(Telemetry.ts.desc())
        .first()
    )
    if not latest:
        return None

    settings = ensure_settings(device_id)
    stats = SlotStatistic.query.filter_by(device_id=device_id, slot=latest.slot).all()
    best = None
    if stats:
        best = max(stats, key=lambda s: s.avg_power)
    current_power = _current_angle_power(stats, latest.angle_deg) if stats else None
    current_power = current_power if current_power is not None else float(latest.p_w or 0.0)

    if best:
        expected_gain_wh = float(best.avg_power) - current_power
        recommended_angle = float(best.angle_deg)
    else:
        expected_gain_wh = 0.0
        recommended_angle = float(latest.angle_deg)

    avg_duration_sec = _estimate_move_duration(device_id)
    motor_power_w = float(settings.motor_power_w or 0.0)
    motor_cost_wh = (motor_power_w * avg_duration_sec) / 3600.0 if avg_duration_sec else 0.0
    net_gain_wh = expected_gain_wh - motor_cost_wh

    max_moves = settings.max_moves_per_hour or 0
    recent_moves = _moves_last_hour(device_id)
    can_move = (max_moves == 0) or (recent_moves < max_moves)
    decision = "MOVE" if (net_gain_wh >= (settings.min_net_gain_wh or 0.0)) and can_move and (recommended_angle != latest.angle_deg) else "HOLD"

    return {
        "slot": int(latest.slot),
        "current_angle": float(latest.angle_deg),
        "recommended_angle": float(recommended_angle),
        "expected_gain_wh": round(expected_gain_wh, 3),
        "motor_cost_wh": round(motor_cost_wh, 3),
        "net_gain_wh": round(net_gain_wh, 3),
        "decision": decision,
    }


def run_analytics_cycle() -> None:
    generate_slot_statistics()
    generate_daily_summaries()
    try:
        from ai_engine import run_ai_jobs

        run_ai_jobs()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("AI jobs failed; continuing")
