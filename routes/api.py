import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, abort, current_app, jsonify, request, session
from sqlalchemy import and_, desc, func

from auth import get_current_user
from database import db
from analytics import (
    best_angle_per_slot,
    heatmap_data,
    movement_efficiency,
    net_gain_projection,
)
from models import (
    CleaningLog,
    Command,
    DailySummary,
    Device,
    DeviceSettings,
    AISummary,
    Alert,
    DeviceFaultLog,
    Event,
    MovementLog,
    SlotStatistic,
    Telemetry,
)
from utils.markdown_render import render_markdown

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _parse_iso(ts_str: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts_str)
    except ValueError as exc:
        raise ValueError("Invalid timestamp format") from exc
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _require_json(expected_type: str) -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if not payload:
        abort(400, description="Invalid or missing JSON payload")
    if payload.get("type") != expected_type:
        abort(400, description="Unexpected payload type")
    return payload


def _security_log(device_id: Optional[str], msg: str) -> None:
    logger = current_app.logger
    security_logger = logger.manager.getLogger("security")
    security_logger.info("%s ip=%s device_id=%s", msg, request.remote_addr, device_id or "")


def _authenticate_device(requested_device_id: Optional[str]) -> Device:
    api_key = request.headers.get("X-API-KEY")
    if not api_key:
        _security_log(requested_device_id, "Missing API key")
        abort(401)

    device = Device.query.filter_by(api_key=api_key).first()
    if not device or not device.is_active:
        _security_log(requested_device_id, "Invalid API key")
        abort(401)

    if requested_device_id and device.device_id != requested_device_id:
        _security_log(requested_device_id, "API key device mismatch")
        abort(401)

    device.last_ip = request.remote_addr
    fw = request.headers.get("X-Firmware-Version")
    if fw:
        device.firmware_version = fw
    db.session.commit()
    return device


def _enforce_data_source(device: Device) -> None:
    mode = (current_app.config.get("DATA_SOURCE") or "DEVICE").upper()
    sim_id = current_app.config.get("SIMULATOR_DEVICE_ID", "AEY-SIM-001")
    if mode == "SIMULATOR" and device.device_id != sim_id:
        abort(403, description="Hardware ingestion disabled in simulator mode")
    if mode == "DEVICE" and device.device_id == sim_id:
        abort(403, description="Simulator ingestion disabled in device mode")


def _dialect_name() -> str:
    bind = db.session.get_bind()
    if bind:
        return bind.dialect.name
    engine = db.get_engine(current_app)
    return engine.dialect.name


def _minute_bucket(column):
    if _dialect_name() == "sqlite":
        return func.strftime("%Y-%m-%d %H:%M:00", column)
    return func.date_trunc("minute", column)


def _day_bucket(column):
    if _dialect_name() == "sqlite":
        return func.date(column)
    return func.date_trunc("day", column)


def _owned_device_or_abort(device_id: str) -> Device:
    user = get_current_user()
    if not user:
        abort(403)
    device = Device.query.filter_by(device_id=device_id, user_id=user.id).first()
    if not device:
        abort(403)
    return device


def _device_status(device: Device) -> str:
    if not device.last_seen:
        return "offline"
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    last_seen = device.last_seen
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return "online" if now - last_seen <= timedelta(minutes=2) else "offline"


def _ensure_settings(device_id: str) -> DeviceSettings:
    settings = DeviceSettings.query.filter_by(device_id=device_id).first()
    if settings:
        if settings.motor_power_w is None:
            settings.motor_power_w = 50.0
        if settings.hold_power_w is None:
            settings.hold_power_w = 2.0
        db.session.commit()
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


def _avg_power_between(device_id: str, start_dt: datetime, end_dt: datetime) -> float:
    avg_power = (
        db.session.query(func.avg(Telemetry.p_w))
        .filter(
            Telemetry.device_id == device_id,
            Telemetry.ts >= start_dt,
            Telemetry.ts < end_dt,
        )
        .scalar()
    )
    return float(avg_power or 0.0)


def _last_24h_power(device_id: str) -> List[Dict[str, Any]]:
    since = datetime.utcnow() - timedelta(hours=24)
    bucket = _minute_bucket(Telemetry.ts).label("bucket")
    rows = (
        db.session.query(bucket, func.avg(Telemetry.p_w))
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= since)
        .group_by(bucket)
        .order_by(bucket.asc())
        .all()
    )
    data = []
    for bucket_value, avg_power in rows:
        if isinstance(bucket_value, datetime):
            ts_str = bucket_value.isoformat()
        else:
            ts_str = str(bucket_value)
        data.append({"time": ts_str, "power": round(avg_power or 0, 2)})
    return data


def _angle_timeline(device_id: str) -> List[Dict[str, Any]]:
    since = datetime.utcnow() - timedelta(hours=24)
    bucket = _minute_bucket(Telemetry.ts).label("bucket")
    rows = (
        db.session.query(bucket, func.avg(Telemetry.angle_deg))
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= since)
        .group_by(bucket)
        .order_by(bucket.asc())
        .all()
    )
    data = []
    for bucket_value, avg_angle in rows:
        ts_str = bucket_value.isoformat() if isinstance(bucket_value, datetime) else str(bucket_value)
        data.append({"time": ts_str, "angle": round(avg_angle or 0, 2)})
    return data


def _daily_energy(device_id: str, days: int = 7) -> List[Dict[str, Any]]:
    start_day = date.today() - timedelta(days=days - 1)
    day_expr = _day_bucket(Telemetry.ts).label("day")
    latest_per_day = (
        db.session.query(day_expr, func.max(Telemetry.ts).label("latest_ts"))
        .filter(Telemetry.device_id == device_id, Telemetry.ts >= datetime.combine(start_day, datetime.min.time()))
        .group_by(day_expr)
        .subquery()
    )

    rows = (
        db.session.query(latest_per_day.c.day, Telemetry.e_wh_today)
        .join(
            Telemetry,
            and_(
                Telemetry.ts == latest_per_day.c.latest_ts,
                _day_bucket(Telemetry.ts) == latest_per_day.c.day,
                Telemetry.device_id == device_id,
            ),
        )
        .order_by(latest_per_day.c.day.asc())
        .all()
    )
    data = []
    for day_value, energy in rows:
        label = day_value.isoformat() if isinstance(day_value, (datetime, date)) else str(day_value)
        data.append({"date": label, "energy_wh": float(energy or 0)})
    return data


def _slot_averages(device_id: str, start_dt: datetime, end_dt: datetime) -> List[Dict[str, Any]]:
    rows = (
        db.session.query(
            Telemetry.slot,
            func.avg(Telemetry.p_w).label("avg_power"),
            func.avg(Telemetry.v_panel).label("avg_voltage"),
            func.avg(Telemetry.i_panel).label("avg_current"),
            func.avg(Telemetry.angle_deg).label("avg_angle"),
        )
        .filter(
            Telemetry.device_id == device_id,
            Telemetry.ts >= start_dt,
            Telemetry.ts < end_dt,
        )
        .group_by(Telemetry.slot)
        .order_by(Telemetry.slot.asc())
        .all()
    )
    return [
        {
            "slot": slot,
            "avg_power": round(avg_power or 0, 2),
            "avg_voltage": round(avg_voltage or 0, 2),
            "avg_current": round(avg_current or 0, 2),
            "avg_angle": round(avg_angle or 0, 2),
        }
        for slot, avg_power, avg_voltage, avg_current, avg_angle in rows
    ]


ALLOWED_COMMANDS = {"set_mode", "set_angle", "set_thresholds", "request_snapshot"}


def _validated_command(cmd: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if cmd not in ALLOWED_COMMANDS:
        abort(400, description="Unsupported command")
    args = args or {}
    if cmd == "set_mode":
        mode = args.get("mode")
        if mode not in {"auto", "manual", "explore"}:
            abort(400, description="Invalid mode")
        return {"mode": mode}
    if cmd == "set_angle":
        try:
            angle = float(args.get("angle_deg"))
        except (TypeError, ValueError):
            abort(400, description="angle_deg required")
        if not 30 <= angle <= 150:
            abort(400, description="angle_deg out of range")
        return {"angle_deg": round(angle, 2)}
    if cmd == "set_thresholds":
        try:
            min_gain = float(args.get("min_net_gain_wh"))
            max_moves = int(args.get("max_moves_per_hour"))
        except (TypeError, ValueError):
            abort(400, description="Invalid thresholds")
        if max_moves < 0:
            abort(400, description="max_moves_per_hour must be >= 0")
        return {"min_net_gain_wh": min_gain, "max_moves_per_hour": max_moves}
    return {}


@api_bp.route("/telemetry", methods=["POST"])
def ingest_telemetry():
    data = _require_json("telemetry")
    device = _authenticate_device(data.get("device_id"))
    _enforce_data_source(device)
    required_fields = [
        "device_id",
        "seq",
        "ts",
        "slot",
        "v_panel",
        "i_panel",
        "p_w",
        "e_wh_today",
        "angle_deg",
        "mode",
        "move_count_today",
        "v_sys_5v",
        "acs_offset_v",
        "rssi",
        "fault_flags",
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        abort(400, description=f"Missing fields: {', '.join(missing)}")

    ts = _parse_iso(str(data["ts"]))

    telemetry = Telemetry(
        device_id=device.device_id,
        seq=int(data["seq"]),
        ts=ts,
        slot=int(data["slot"]),
        v_panel=float(data["v_panel"]),
        i_panel=float(data["i_panel"]),
        p_w=float(data["p_w"]),
        e_wh_today=float(data["e_wh_today"]),
        angle_deg=float(data["angle_deg"]),
        mode=str(data["mode"]),
        move_count_today=int(data["move_count_today"]),
        v_sys_5v=float(data["v_sys_5v"]),
        acs_offset_v=float(data["acs_offset_v"]),
        rssi=int(data["rssi"]),
        fault_flags=int(data["fault_flags"]),
    )
    device.last_seen = datetime.utcnow()
    db.session.add(telemetry)
    db.session.commit()

    current_app.logger.info("Telemetry stored for %s seq=%s", device.device_id, telemetry.seq)
    return jsonify({"status": "ok"})


@api_bp.route("/event", methods=["POST"])
def ingest_event():
    data = _require_json("event")
    device = _authenticate_device(data.get("device_id"))
    _enforce_data_source(device)
    required_fields = ["device_id", "ts"]
    if not data.get("event_type") and not data.get("event"):
        abort(400, description="event_type or event required")
    if "data_json" not in data and "data" not in data:
        abort(400, description="data_json or data required")
    missing = [f for f in required_fields if f not in data]
    if missing:
        abort(400, description=f"Missing fields: {', '.join(missing)}")

    ts = _parse_iso(str(data["ts"]))
    event_type = str(data.get("event_type") or data.get("event"))
    payload_data = data.get("data_json", data.get("data"))
    event = Event(
        device_id=device.device_id,
        ts=ts,
        event_type=event_type,
        data_json=json.dumps(payload_data),
    )
    device.last_seen = datetime.utcnow()
    db.session.add(event)

    if event.event_type.lower() == "move":
        settings = _ensure_settings(device.device_id)
        details = payload_data or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                details = {}
        try:
            from_angle = float(details.get("from_angle")) if details.get("from_angle") is not None else None
        except (TypeError, ValueError):
            from_angle = None
        try:
            to_angle = float(details.get("to_angle")) if details.get("to_angle") is not None else None
        except (TypeError, ValueError):
            to_angle = None
        try:
            duration = float(details.get("move_duration_sec")) if details.get("move_duration_sec") is not None else None
        except (TypeError, ValueError):
            duration = None
        try:
            est_power = float(details.get("motor_estimated_power_w")) if details.get("motor_estimated_power_w") is not None else None
        except (TypeError, ValueError):
            est_power = None
        motor_power = est_power if est_power is not None else float(settings.motor_power_w or 0.0)
        energy_cost_wh = (motor_power * duration / 3600.0) if duration else None
        triggered_by = details.get("triggered_by") or "auto"
        move_log = MovementLog(
            device_id=device.device_id,
            ts=ts,
            from_angle=from_angle,
            to_angle=to_angle,
            move_duration_sec=duration,
            motor_estimated_power_w=est_power if est_power is not None else motor_power,
            energy_cost_wh=energy_cost_wh,
            triggered_by=triggered_by,
        )
        db.session.add(move_log)

    db.session.commit()

    current_app.logger.info("Event stored for %s type=%s", device.device_id, event.event_type)
    return jsonify({"status": "ok"})


@api_bp.route("/ai/latest/<device_id>", methods=["GET"])
def latest_ai_summary(device_id: str):
    _owned_device_or_abort(device_id)
    summary = (
        AISummary.query.filter_by(device_id=device_id)
        .order_by(AISummary.generated_at.desc())
        .first()
    )
    if not summary:
        abort(404, description="No AI summary yet")
    try:
        payload = json.loads(summary.summary_json)
    except json.JSONDecodeError:
        payload = summary.summary_json
    if isinstance(payload, dict):
        # ensure sanitized HTML is available and raw text is not exposed
        payload.pop("explanation", None)
        if not payload.get("explanation_html"):
            payload["explanation_html"] = summary.explanation_html or render_markdown(summary.explanation_raw or "")
        if not payload.get("recommendations_html"):
            payload["recommendations_html"] = summary.recommendations_html or render_markdown(
                "\n".join(f"- {rec}" for rec in payload.get("recommendations", [])) if payload.get("recommendations") else ""
            )
    return jsonify(payload)


@api_bp.route("/alerts/<device_id>", methods=["GET"])
def get_alerts(device_id: str):
    _owned_device_or_abort(device_id)
    alerts = Alert.query.filter_by(device_id=device_id, cleared=False).order_by(Alert.created_at.desc()).all()
    payload = []
    for a in alerts:
        detail_html = a.detail_html or render_markdown(a.detail or "")
        payload.append(
            {
                "id": a.id,
                "severity": a.severity,
                "title": a.title,
                "detail_html": detail_html,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
        )
    return jsonify(payload)


@api_bp.route("/alerts/clear/<int:alert_id>", methods=["POST"])
def clear_alert(alert_id: int):
    user = get_current_user()
    if not user:
        abort(403)
    alert = Alert.query.filter_by(id=alert_id).first()
    if not alert:
        abort(404)
    device = Device.query.filter_by(device_id=alert.device_id, user_id=user.id).first()
    if not device:
        abort(403)
    alert.cleared = True
    db.session.commit()
    return jsonify({"status": "cleared", "alert_id": alert_id})


@api_bp.route("/faults/<device_id>", methods=["GET"])
def fault_logs(device_id: str):
    _owned_device_or_abort(device_id)
    logs = (
        DeviceFaultLog.query.filter_by(device_id=device_id)
        .order_by(DeviceFaultLog.ts.desc())
        .limit(100)
        .all()
    )
    payload = []
    for log in logs:
        try:
            details = json.loads(log.details_json or "{}")
        except json.JSONDecodeError:
            details = log.details_json
        payload.append(
            {
                "id": log.id,
                "ts": log.ts.isoformat(),
                "fault_type": log.fault_type,
                "severity": log.severity,
                "details": details,
                "correlated_move_id": log.correlated_move_id,
            }
        )
    return jsonify(payload)


@api_bp.route("/cmd/<device_id>", methods=["GET"])
def next_command(device_id: str):
    device = _authenticate_device(device_id)
    cmd = (
        Command.query.filter_by(device_id=device_id, sent=False, acknowledged=False)
        .order_by(Command.created_at.asc())
        .first()
    )
    if not cmd:
        return jsonify({"cmd": None})

    cmd.sent = True
    db.session.commit()

    payload = {"cmd_id": cmd.id, "cmd": cmd.cmd}
    if cmd.args_json:
        try:
            payload["args"] = json.loads(cmd.args_json)
        except json.JSONDecodeError:
            payload["args"] = cmd.args_json
    return jsonify(payload)


@api_bp.route("/cmd_ack", methods=["POST"])
def command_ack():
    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    if not device_id:
        abort(400, description="device_id required")
    device = _authenticate_device(device_id)
    cmd_id = data.get("cmd_id")
    if not cmd_id:
        abort(400, description="cmd_id required")
    cmd = Command.query.filter_by(id=cmd_id, device_id=device.device_id).first()
    if not cmd:
        abort(404, description="Command not found")
    cmd.acknowledged = True
    cmd.acknowledged_at = datetime.utcnow()
    cmd.sent = True
    device.last_seen = datetime.utcnow()
    db.session.commit()
    current_app.logger.info("Command ack from %s cmd_id=%s", device.device_id, cmd_id)
    return jsonify({"status": "ok"})


@api_bp.route("/latest/<device_id>", methods=["GET"])
def latest_telemetry(device_id: str):
    device = _owned_device_or_abort(device_id)
    telemetry = Telemetry.query.filter_by(device_id=device_id).order_by(Telemetry.ts.desc()).first()
    if not telemetry:
        return jsonify(
            {
                "device_id": device.device_id,
                "seq": None,
                "ts": None,
                "slot": None,
                "v_panel": None,
                "i_panel": None,
                "p_w": None,
                "e_wh_today": None,
                "angle_deg": None,
                "mode": None,
                "move_count_today": None,
                "v_sys_5v": None,
                "acs_offset_v": None,
                "rssi": None,
                "fault_flags": None,
                "last_seen": device.last_seen.isoformat() if device.last_seen else None,
                "status": _device_status(device),
                "has_data": False,
            }
        )

    data = {
        "device_id": telemetry.device_id,
        "seq": telemetry.seq,
        "ts": telemetry.ts.isoformat(),
        "slot": telemetry.slot,
        "v_panel": telemetry.v_panel,
        "i_panel": telemetry.i_panel,
        "p_w": telemetry.p_w,
        "e_wh_today": telemetry.e_wh_today,
        "angle_deg": telemetry.angle_deg,
        "mode": telemetry.mode,
        "move_count_today": telemetry.move_count_today,
        "v_sys_5v": telemetry.v_sys_5v,
        "acs_offset_v": telemetry.acs_offset_v,
        "rssi": telemetry.rssi,
        "fault_flags": telemetry.fault_flags,
        "last_seen": telemetry.device.last_seen.isoformat() if telemetry.device.last_seen else None,
    }
    return jsonify(data)


@api_bp.route("/chart/power/<device_id>", methods=["GET"])
def chart_power(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(_last_24h_power(device_id))


@api_bp.route("/chart/energy/<device_id>", methods=["GET"])
def chart_energy(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(_daily_energy(device_id))


@api_bp.route("/chart/angle/<device_id>", methods=["GET"])
def chart_angle(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(_angle_timeline(device_id))


@api_bp.route("/device/status/<device_id>", methods=["GET"])
def device_status(device_id: str):
    device = _owned_device_or_abort(device_id)
    return jsonify({"device_id": device_id, "status": _device_status(device), "last_seen": device.last_seen.isoformat() if device.last_seen else None})


@api_bp.route("/devices", methods=["GET"])
def list_devices():
    user = get_current_user()
    if not user:
        abort(403)
    devices = Device.query.filter_by(user_id=user.id).order_by(Device.device_id.asc()).all()
    payload = []
    for d in devices:
        payload.append(
            {
                "device_id": d.device_id,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "status": _device_status(d),
            }
        )
    return jsonify(payload)


@api_bp.route("/history/<device_id>", methods=["GET"])
def history(device_id: str):
    _owned_device_or_abort(device_id)
    date_str = request.args.get("date")
    if not date_str:
        abort(400, description="date query param required")
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        abort(400, description="Invalid date format")

    start_dt = datetime.combine(day, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    telemetry_rows = (
        Telemetry.query.filter(
            Telemetry.device_id == device_id,
            Telemetry.ts >= start_dt,
            Telemetry.ts < end_dt,
        )
        .order_by(Telemetry.ts.asc())
        .all()
    )

    data = [
        {
            "ts": row.ts.isoformat(),
            "p_w": row.p_w,
            "v_panel": row.v_panel,
            "i_panel": row.i_panel,
            "e_wh_today": row.e_wh_today,
            "angle_deg": row.angle_deg,
            "slot": row.slot,
        }
        for row in telemetry_rows
    ]

    slot_avgs = _slot_averages(device_id, start_dt, end_dt)

    return jsonify({"telemetry": data, "slot_averages": slot_avgs})


@api_bp.route("/send_cmd/<device_id>", methods=["POST"])
def send_command(device_id: str):
    device = _owned_device_or_abort(device_id)
    payload = request.get_json(silent=True) or {}
    cmd_name = payload.get("cmd")
    if not cmd_name:
        abort(400, description="cmd required")
    args = _validated_command(cmd_name, payload.get("args") or {})
    cmd = Command(device_id=device.device_id, cmd=cmd_name, args_json=json.dumps(args) if args else None)
    db.session.add(cmd)
    db.session.commit()
    current_app.logger.info("Command queued device=%s cmd=%s id=%s", device.device_id, cmd_name, cmd.id)
    return jsonify({"cmd_id": cmd.id})


@api_bp.route("/settings/<device_id>", methods=["GET", "POST"])
def device_settings(device_id: str):
    device = _owned_device_or_abort(device_id)
    settings = _ensure_settings(device.device_id)
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode")
        if mode:
            if mode not in {"auto", "manual", "explore"}:
                abort(400, description="Invalid mode")
            settings.mode = mode
        if "min_net_gain_wh" in payload:
            try:
                settings.min_net_gain_wh = float(payload.get("min_net_gain_wh"))
            except (TypeError, ValueError):
                abort(400, description="Invalid min_net_gain_wh")
        if "max_moves_per_hour" in payload:
            try:
                settings.max_moves_per_hour = int(payload.get("max_moves_per_hour"))
            except (TypeError, ValueError):
                abort(400, description="Invalid max_moves_per_hour")
        if "motor_power_w" in payload:
            try:
                settings.motor_power_w = float(payload.get("motor_power_w"))
            except (TypeError, ValueError):
                abort(400, description="Invalid motor_power_w")
        if "hold_power_w" in payload:
            try:
                settings.hold_power_w = float(payload.get("hold_power_w"))
            except (TypeError, ValueError):
                abort(400, description="Invalid hold_power_w")
        settings.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify(
        {
            "device_id": settings.device_id,
            "mode": settings.mode,
            "min_net_gain_wh": settings.min_net_gain_wh,
            "max_moves_per_hour": settings.max_moves_per_hour,
            "motor_power_w": settings.motor_power_w,
            "hold_power_w": settings.hold_power_w,
            "updated_at": settings.updated_at.isoformat() if settings.updated_at else None,
        }
    )


@api_bp.route("/cmd_history/<device_id>", methods=["GET"])
def command_history(device_id: str):
    _owned_device_or_abort(device_id)
    rows = (
        Command.query.filter_by(device_id=device_id)
        .order_by(Command.created_at.desc())
        .limit(50)
        .all()
    )
    history = []
    for row in rows:
        try:
            args = json.loads(row.args_json) if row.args_json else {}
        except json.JSONDecodeError:
            args = row.args_json
        if row.acknowledged:
            status = "executed"
        elif row.sent:
            status = "sent"
        else:
            status = "pending"
        history.append(
            {
                "id": row.id,
                "cmd": row.cmd,
                "args": args,
                "status": status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
            }
        )
    return jsonify(history)


@api_bp.route("/cleaning/<device_id>", methods=["POST"])
def record_cleaning(device_id: str):
    device = _owned_device_or_abort(device_id)
    payload = request.get_json(silent=True) or {}
    cleaning_type = payload.get("cleaning_type")
    if cleaning_type not in {"manual", "auto_wiper"}:
        abort(400, description="Invalid cleaning_type")
    note = payload.get("note", "")
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(hours=2)
    avg_power_before = _avg_power_between(device.device_id, start_dt, end_dt)
    energy_before_wh = avg_power_before * 2.0
    log = CleaningLog(
        device_id=device.device_id,
        cleaning_type=cleaning_type,
        note=note,
        energy_before_wh=energy_before_wh,
    )
    db.session.add(log)
    db.session.commit()
    current_app.logger.info(
        "Cleaning recorded device=%s type=%s energy_before=%.2f",
        device.device_id,
        cleaning_type,
        energy_before_wh,
    )
    return jsonify(
        {
            "id": log.id,
            "energy_before_wh": energy_before_wh,
            "cleaned_at": log.cleaned_at.isoformat() if log.cleaned_at else None,
        }
    )


@api_bp.route("/select_device", methods=["POST"])
def select_device():
    user = get_current_user()
    if not user:
        abort(403)
    payload = request.get_json(silent=True) or {}
    device_id = payload.get("device_id")
    if not device_id:
        abort(400, description="device_id required")
    device = Device.query.filter_by(device_id=device_id, user_id=user.id).first()
    if not device:
        abort(403)
    session["selected_device_id"] = device_id
    return jsonify({"status": "ok", "device_id": device_id})


@api_bp.route("/analytics/heatmap/<device_id>", methods=["GET"])
def analytics_heatmap(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(heatmap_data(device_id))


@api_bp.route("/analytics/best_angles/<device_id>", methods=["GET"])
def analytics_best_angles(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(best_angle_per_slot(device_id))


@api_bp.route("/analytics/efficiency/<device_id>", methods=["GET"])
def analytics_efficiency(device_id: str):
    _owned_device_or_abort(device_id)
    return jsonify(movement_efficiency(device_id))


@api_bp.route("/analytics/daily/<device_id>", methods=["GET"])
def analytics_daily(device_id: str):
    _owned_device_or_abort(device_id)
    rows = (
        DailySummary.query.filter_by(device_id=device_id)
        .order_by(DailySummary.date.desc())
        .limit(7)
        .all()
    )
    payload = [
        {
            "date": row.date.isoformat(),
            "energy_wh": float(row.energy_wh or 0.0),
            "move_count": row.move_count,
            "efficiency_ratio": float(row.efficiency_ratio) if row.efficiency_ratio is not None else None,
        }
        for row in reversed(rows)
    ]
    return jsonify(payload)


@api_bp.route("/analytics/net_gain/<device_id>", methods=["GET"])
def analytics_net_gain(device_id: str):
    _owned_device_or_abort(device_id)
    projection = net_gain_projection(device_id)
    if not projection:
        return jsonify({})
    return jsonify(projection)
