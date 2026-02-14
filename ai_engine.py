import json
import logging
import math
import os
from datetime import datetime, timedelta
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types
from sqlalchemy import and_, func

from database import db
from utils.markdown_render import render_markdown
from models import (
    AISummary,
    Alert,
    DailySummary,
    Device,
    DeviceFaultLog,
    Event,
    MovementLog,
    SlotStatistic,
    Telemetry,
)

logger = logging.getLogger(__name__)


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _slot_power(device_id: str, start_dt: datetime, end_dt: datetime) -> Dict[int, Tuple[float, int]]:
    rows = (
        db.session.query(Telemetry.slot, func.avg(Telemetry.p_w), func.count(Telemetry.id))
        .filter(
            Telemetry.device_id == device_id,
            Telemetry.ts >= start_dt,
            Telemetry.ts < end_dt,
        )
        .group_by(Telemetry.slot)
        .all()
    )
    return {int(slot): (float(avg or 0.0), int(count or 0)) for slot, avg, count in rows}


def _dust_shading(device_id: str) -> Dict[str, float]:
    now = datetime.utcnow()
    recent_start = now - timedelta(days=2)
    baseline_start = now - timedelta(days=9)
    baseline_end = now - timedelta(days=2)

    baseline = _slot_power(device_id, baseline_start, baseline_end)
    recent = _slot_power(device_id, recent_start, now)

    ratios: List[float] = []
    for slot, (base_avg, _) in baseline.items():
        if base_avg <= 0:
            continue
        rec_avg, _ = recent.get(slot, (0.0, 0))
        ratios.append(rec_avg / base_avg)

    if not ratios:
        return {"dust_probability": 0.0, "shading_probability": 0.0}

    mean_ratio = mean(ratios)
    std_ratio = pstdev(ratios) if len(ratios) > 1 else 0.0
    dust_probability = _clamp((1.0 - mean_ratio) * 1.2) * _clamp(1.0 - (std_ratio / 0.25))
    shading_probability = _clamp(std_ratio / 0.3) * _clamp(1.0 - mean_ratio)
    return {
        "dust_probability": round(dust_probability, 3),
        "shading_probability": round(shading_probability, 3),
        "mean_ratio": round(mean_ratio, 3),
        "std_ratio": round(std_ratio, 3),
    }


def _sensor_health(device_id: str) -> Dict[str, Any]:
    now = datetime.utcnow()
    last_day = now - timedelta(days=1)
    last_week = now - timedelta(days=7)

    day_rows = (
        Telemetry.query.with_entities(
            Telemetry.v_panel,
            Telemetry.i_panel,
            Telemetry.p_w,
            Telemetry.acs_offset_v,
            Telemetry.v_sys_5v,
        )
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= last_day)
        .all()
    )
    week_offsets = (
        Telemetry.query.with_entities(Telemetry.acs_offset_v)
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= last_week)
        .all()
    )

    if not day_rows:
        return {"score": 50.0, "zero_current_ratio": 0.0, "spike_ratio": 0.0, "offset_drift": 0.0}

    zero_current = 0
    power_values: List[float] = []
    offsets: List[float] = []
    sys_voltages: List[float] = []

    for v_panel, i_panel, p_w, acs_offset_v, v_sys in day_rows:
        if v_panel and v_panel > 2.0 and (i_panel is not None) and i_panel < 0.05:
            zero_current += 1
        power_values.append(float(p_w or 0.0))
        offsets.append(float(acs_offset_v or 0.0))
        sys_voltages.append(float(v_sys or 0.0))

    zero_current_ratio = zero_current / max(len(day_rows), 1)
    avg_power = mean(power_values) if power_values else 0.0
    std_power = pstdev(power_values) if len(power_values) > 1 else 0.0
    spikes = len([p for p in power_values if p > avg_power + (3 * std_power) and std_power > 0])
    spike_ratio = spikes / max(len(power_values), 1)

    week_offsets_vals = [float(o[0]) for o in week_offsets] if week_offsets else offsets
    baseline_offset = mean(week_offsets_vals) if week_offsets_vals else (mean(offsets) if offsets else 0.0)
    offset_drift = abs((mean(offsets) if offsets else baseline_offset) - baseline_offset)

    score = 100.0
    score -= zero_current_ratio * 40.0
    score -= spike_ratio * 30.0
    score -= min(offset_drift * 50.0, 30.0)
    low_voltage_hits = len([v for v in sys_voltages if v < 4.7])
    score -= (low_voltage_hits / max(len(sys_voltages), 1)) * 20.0
    score = _clamp(score / 100.0, 0.0, 1.0) * 100.0

    return {
        "score": round(score, 2),
        "zero_current_ratio": round(zero_current_ratio, 3),
        "spike_ratio": round(spike_ratio, 3),
        "offset_drift": round(offset_drift, 3),
    }


def _movement_cost(device_id: str) -> Dict[str, Any]:
    since = datetime.utcnow() - timedelta(hours=6)
    logs = (
        MovementLog.query.filter(MovementLog.device_id == device_id, MovementLog.ts >= since)
        .order_by(MovementLog.ts.asc())
        .all()
    )
    if not logs:
        return {"moves": 0, "wasted_pct": 0.0, "avg_gain": 0.0}

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

    wasted_pct = (wasted / len(logs) * 100.0) if logs else 0.0
    avg_gain = sum(gains) / len(gains) if gains else 0.0
    return {"moves": len(logs), "wasted_pct": round(wasted_pct, 2), "avg_gain": round(avg_gain, 3)}


def _power_rail_analysis(device_id: str) -> Dict[str, Any]:
    now = datetime.utcnow()
    since = now - timedelta(days=1)
    moves = (
        MovementLog.query.filter(MovementLog.device_id == device_id, MovementLog.ts >= since)
        .order_by(MovementLog.ts.asc())
        .all()
    )
    events = (
        Event.query.filter(Event.device_id == device_id, Event.ts >= since)
        .order_by(Event.ts.asc())
        .all()
    )

    resets = [e for e in events if e.event_type.lower() == "reset"]
    low_supply_events = [e for e in events if e.event_type.lower() in {"low_supply", "brownout"}]

    power_rail_hits = 0
    correlated_logs: List[DeviceFaultLog] = []

    for move in moves:
        window_start = move.ts
        window_end = move.ts + timedelta(seconds=20)
        dip = (
            Telemetry.query.filter(
                Telemetry.device_id == device_id,
                Telemetry.ts >= window_start,
                Telemetry.ts <= window_end,
                Telemetry.v_sys_5v < 4.7,
            )
            .order_by(Telemetry.v_sys_5v.asc())
            .first()
        )
        reset_nearby = next((r for r in resets if 0 <= (r.ts - move.ts).total_seconds() <= 10), None)
        low_supply_nearby = next((r for r in low_supply_events if 0 <= (r.ts - move.ts).total_seconds() <= 10), None)
        if dip or reset_nearby or low_supply_nearby:
            power_rail_hits += 1
            details = {
                "move_ts": move.ts.isoformat(),
                "dip_voltage": dip.v_sys_5v if dip else None,
                "reset_ts": reset_nearby.ts.isoformat() if reset_nearby else None,
                "low_supply": low_supply_nearby.ts.isoformat() if low_supply_nearby else None,
            }
            log = _record_fault(device_id, window_end, "power_rail_drop", "critical", details, correlated_move_id=move.id)
            if log:
                correlated_logs.append(log)

    reset_frequency = len(resets)
    power_rail_risk = _clamp(power_rail_hits / max(len(moves), 1) * 1.2)

    return {
        "power_rail_risk": round(power_rail_risk, 3),
        "reset_frequency": reset_frequency,
        "fault_logs": correlated_logs,
    }


def _rtc_stability(device_id: str) -> Dict[str, Any]:
    now = datetime.utcnow()
    since = now - timedelta(days=1)
    rtc_events = (
        Event.query.filter(Event.device_id == device_id, Event.ts >= since, Event.event_type.ilike("rtc_lost"))
        .order_by(Event.ts.asc())
        .all()
    )
    rtc_count = len(rtc_events)
    score = 100 - max(0, (rtc_count - 3) * 10)
    score = max(score, 0)
    if rtc_count > 3:
        _record_fault(device_id, rtc_events[-1].ts, "rtc_unstable", "warn", {"count_last_day": rtc_count})
    return {"rtc_reliability_score": score, "rtc_events": rtc_events}


def _sensor_fault_patterns(device_id: str) -> List[DeviceFaultLog]:
    now = datetime.utcnow()
    since = now - timedelta(days=1)
    events = (
        Event.query.filter(Event.device_id == device_id, Event.ts >= since, Event.event_type.ilike("sensor_fault"))
        .order_by(Event.ts.asc())
        .all()
    )
    if len(events) < 2:
        return []
    faults: List[DeviceFaultLog] = []
    window = timedelta(hours=1)
    for i, ev in enumerate(events):
        same_hour = [e for e in events if 0 <= (e.ts - ev.ts).total_seconds() <= window.total_seconds()]
        if len(same_hour) >= 2:
            log = _record_fault(
                device_id,
                ev.ts,
                "sensor_wiring_instability",
                "warn",
                {"count_hour": len(same_hour), "example_ts": ev.ts.isoformat()},
            )
            if log:
                faults.append(log)
            break
    return faults


def _record_fault(
    device_id: str,
    ts: datetime,
    fault_type: str,
    severity: str,
    details: Optional[Dict[str, Any]] = None,
    correlated_move_id: Optional[int] = None,
) -> Optional[DeviceFaultLog]:
    existing = (
        DeviceFaultLog.query.filter(
            DeviceFaultLog.device_id == device_id,
            DeviceFaultLog.fault_type == fault_type,
            DeviceFaultLog.ts >= ts - timedelta(seconds=30),
            DeviceFaultLog.ts <= ts + timedelta(seconds=30),
        )
        .order_by(DeviceFaultLog.ts.desc())
        .first()
    )
    if existing:
        return existing
    log = DeviceFaultLog(
        device_id=device_id,
        ts=ts,
        fault_type=fault_type,
        severity=severity,
        details_json=json.dumps(details or {}),
        correlated_move_id=correlated_move_id,
    )
    db.session.add(log)
    return log


def _forecast_next_hour(device_id: str) -> Dict[str, Any]:
    now = datetime.utcnow()
    one_hour = now - timedelta(hours=1)
    two_hours = now - timedelta(hours=2)
    hour_rows = (
        Telemetry.query.with_entities(Telemetry.ts, Telemetry.p_w, Telemetry.e_wh_today)
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= two_hours)
        .order_by(Telemetry.ts.asc())
        .all()
    )
    if not hour_rows:
        return {"predicted_wh": 0.0, "confidence": 0.0}

    recent_wh = [float(r.e_wh_today or 0.0) for r in hour_rows]
    power_vals = [float(r.p_w or 0.0) for r in hour_rows if r.ts >= one_hour]
    avg_power = mean(power_vals) if power_vals else 0.0
    predicted_wh = avg_power * 1.0

    trend = 0.0
    if len(recent_wh) >= 2:
        trend = recent_wh[-1] - recent_wh[0]
    predicted_wh = max(predicted_wh + trend * 0.1, 0.0)

    confidence = _clamp(len(power_vals) / 60.0, 0.0, 1.0) * 100.0

    # adjust with weekday baseline
    latest_summary = (
        DailySummary.query.filter_by(device_id=device_id)
        .order_by(DailySummary.date.desc())
        .limit(4)
        .all()
    )
    if latest_summary:
        avg_daily = mean([float(s.energy_wh or 0.0) for s in latest_summary])
        if avg_daily:
            predicted_wh = _clamp(predicted_wh, 0.0, avg_daily)
    return {"predicted_wh": round(predicted_wh, 2), "confidence": round(confidence, 1)}


def _efficiency_score(device_id: str, sensor_health_score: float) -> Dict[str, Any]:
    latest = Telemetry.query.filter_by(device_id=device_id).order_by(Telemetry.ts.desc()).first()
    if not latest:
        return {"efficiency_score": 0.0, "best_slot_power": 0.0}

    stats = SlotStatistic.query.filter_by(device_id=device_id, slot=latest.slot).all()
    best_power = max([s.avg_power for s in stats], default=0.0)
    ratio = (latest.p_w / best_power) if best_power > 0 else 0.0

    move_stats = _movement_cost(device_id)
    wasted_pct = move_stats.get("wasted_pct", 0.0)

    score = 100.0
    score -= (1 - _clamp(ratio, 0.0, 1.0)) * 40.0
    score -= min(wasted_pct * 0.5, 30.0)
    score -= (100.0 - sensor_health_score) * 0.3
    score = _clamp(score / 100.0, 0.0, 1.0) * 100.0

    return {
        "efficiency_score": round(score, 2),
        "best_slot_power": round(best_power, 2),
        "wasted_moves_percent": wasted_pct,
        "avg_gain_wh": move_stats.get("avg_gain", 0.0),
    }


def _raise_alert(device_id: str, severity: str, title: str, detail: str) -> Alert:
    existing = (
        Alert.query.filter_by(device_id=device_id, title=title, cleared=False)
        .order_by(Alert.created_at.desc())
        .first()
    )
    if existing:
        existing.detail = detail
        existing.detail_html = render_markdown(detail)
        return existing
    alert = Alert(
        device_id=device_id,
        severity=severity,
        title=title,
        detail=detail,
        detail_html=render_markdown(detail),
    )
    db.session.add(alert)
    return alert


def _generate_recommendations(metrics: Dict[str, Any]) -> List[str]:
    recs: List[str] = []
    if metrics["dust"]["dust_probability"] > 0.6:
        recs.append("Clean panel to remove dust accumulation")
    if metrics["dust"]["shading_probability"] > 0.6:
        recs.append("Check for recurring shading during specific slots")
    if metrics["power_rail"]["power_rail_risk"] > 0.4:
        recs.append("Add capacitor near servo supply and verify wiring")
    if metrics["sensor_health"]["score"] < 70:
        recs.append("Check sensor wiring and recalibrate current offset")
    if metrics["efficiency"]["efficiency_score"] < 60:
        recs.append("Limit movements to high-yield slots to save energy")
    if metrics["rtc"]["rtc_reliability_score"] < 80:
        recs.append("Replace RTC battery to avoid time drift")
    if not recs:
        recs.append("System healthy. Maintain normal operation.")
    return recs


def _call_gemini_explainer(metrics: Dict[str, Any]) -> Optional[str]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(tools=[grounding_tool])
        prompt = (
            "Explain solar controller diagnostics in concise terms using these metrics: "
            f"dust={metrics['dust']['dust_probability']}, "
            f"shading={metrics['dust']['shading_probability']}, "
            f"sensor_health={metrics['sensor_health']['score']}, "
            f"power_rail_risk={metrics['power_rail']['power_rail_risk']}, "
            f"forecast_wh={metrics['forecast']['predicted_wh']}, "
            f"efficiency={metrics['efficiency']['efficiency_score']}"
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=config,
        )
        return resp.text if hasattr(resp, "text") else None
    except Exception:  # noqa: BLE001
        logger.exception("Gemini explanation failed")
        return None


def _assemble_summary(
    device: Device,
    metrics: Dict[str, Any],
    explanation_html: Optional[str],
    recommendations_html: Optional[str],
) -> Dict[str, Any]:
    latest = Telemetry.query.filter_by(device_id=device.device_id).order_by(Telemetry.ts.desc()).first()
    today_energy = 0.0
    move_count = 0
    if latest:
        today_energy = float(latest.e_wh_today or 0.0)
        move_count = int(latest.move_count_today or 0)

    fault_logs = (
        DeviceFaultLog.query.filter_by(device_id=device.device_id)
        .order_by(DeviceFaultLog.ts.desc())
        .limit(20)
        .all()
    )
    fault_payload = []
    for log in fault_logs:
        try:
            details = json.loads(log.details_json or "{}")
        except json.JSONDecodeError:
            details = log.details_json
        fault_payload.append(
            {
                "ts": log.ts.isoformat(),
                "fault_type": log.fault_type,
                "severity": log.severity,
                "details": details,
            }
        )

    alerts = Alert.query.filter_by(device_id=device.device_id, cleared=False).order_by(Alert.created_at.desc()).all()
    alerts_payload = [
        {
            "id": a.id,
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
            "created_at": a.created_at.isoformat(),
        }
        for a in alerts
    ]

    return {
        "device_id": device.device_id,
        "generated_at": datetime.utcnow().isoformat(),
        "today": {"energy_wh": today_energy, "move_count": move_count},
        "diagnostics": metrics,
        "alerts": alerts_payload,
        "recommendations": metrics.get("recommendations", []),
        "recommended_action": metrics.get("recommendations", [None])[0],
        "forecast": metrics.get("forecast", {}),
        "explanation_html": explanation_html,
        "recommendations_html": recommendations_html,
        "reset_frequency": metrics["power_rail"].get("reset_frequency", 0),
        "rtc_reliability_score": metrics["rtc"].get("rtc_reliability_score", 100),
        "power_rail_risk": metrics["power_rail"].get("power_rail_risk", 0.0),
        "faults": fault_payload,
    }


def _analyze_forensics(device_id: str) -> Dict[str, Any]:
    power_rail = _power_rail_analysis(device_id)
    rtc = _rtc_stability(device_id)
    sensor_faults = _sensor_fault_patterns(device_id)
    faults = []
    faults.extend(power_rail.get("fault_logs", []))
    faults.extend(sensor_faults)
    return {"power_rail": power_rail, "rtc": rtc, "faults": faults}


def run_ai_jobs() -> None:
    devices = Device.query.filter_by(is_active=True).all()
    for device in devices:
        try:
            dust_metrics = _dust_shading(device.device_id)
            sensor_health = _sensor_health(device.device_id)
            forecast = _forecast_next_hour(device.device_id)
            efficiency = _efficiency_score(device.device_id, sensor_health.get("score", 0.0))
            forensics = _analyze_forensics(device.device_id)

            metrics = {
                "dust": dust_metrics,
                "sensor_health": sensor_health,
                "forecast": forecast,
                "efficiency": efficiency,
                "power_rail": forensics.get("power_rail", {}),
                "rtc": forensics.get("rtc", {}),
            }

            if metrics["power_rail"].get("power_rail_risk", 0) > 0.4:
                _raise_alert(
                    device.device_id,
                    "critical",
                    "Power rail instability",
                    "Servo movement followed by supply dip or reset detected.",
                )
            if metrics["rtc"].get("rtc_reliability_score", 100) < 70:
                _raise_alert(device.device_id, "warn", "RTC instability", "Multiple RTC loss events detected today.")

            recommendations = _generate_recommendations(
                {
                    "dust": dust_metrics,
                    "sensor_health": sensor_health,
                    "power_rail": metrics["power_rail"],
                    "efficiency": efficiency,
                    "rtc": metrics["rtc"],
                }
            )
            metrics["recommendations"] = recommendations

            explanation = _call_gemini_explainer(metrics)
            explanation_raw = explanation or None
            explanation_html = render_markdown(explanation_raw) if explanation_raw else ""
            recommendations_md = "\n".join(f"- {rec}" for rec in recommendations) if recommendations else "_No recommendations available._"
            recommendations_html = render_markdown(recommendations_md)

            summary = _assemble_summary(device, metrics, explanation_html, recommendations_html)
            summary_row = AISummary(
                device_id=device.device_id,
                summary_json=json.dumps(summary),
                explanation_raw=explanation_raw,
                explanation_html=explanation_html,
                recommendations_html=recommendations_html,
            )
            db.session.add(summary_row)
        except Exception:  # noqa: BLE001
            logger.exception("AI job failed for device %s", device.device_id)
            db.session.rollback()
        else:
            db.session.commit()
