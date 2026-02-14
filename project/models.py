from datetime import date, datetime

from database import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    devices = db.relationship("Device", backref="owner", lazy=True)


class Device(db.Model):
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=True)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen = db.Column(db.DateTime, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    api_key = db.Column(db.String(128), unique=True, nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_ip = db.Column(db.String(64), nullable=True)
    firmware_version = db.Column(db.String(64), nullable=True)

    telemetry = db.relationship("Telemetry", backref="device", lazy=True)
    events = db.relationship("Event", backref="device", lazy=True)
    commands = db.relationship("Command", backref="device", lazy=True)
    settings = db.relationship("DeviceSettings", backref="device", uselist=False, lazy=True)
    cleaning_logs = db.relationship("CleaningLog", backref="device", lazy=True)
    ai_summaries = db.relationship("AISummary", backref="device", lazy=True)
    alerts = db.relationship("Alert", backref="device", lazy=True)
    fault_logs = db.relationship("DeviceFaultLog", backref="device", lazy=True)


class Telemetry(db.Model):
    __tablename__ = "telemetry"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    ts = db.Column(db.DateTime, nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    v_panel = db.Column(db.Float, nullable=False)
    i_panel = db.Column(db.Float, nullable=False)
    p_w = db.Column(db.Float, nullable=False)
    e_wh_today = db.Column(db.Float, nullable=False)
    angle_deg = db.Column(db.Float, nullable=False)
    mode = db.Column(db.String(32), nullable=False)
    move_count_today = db.Column(db.Integer, nullable=False)
    v_sys_5v = db.Column(db.Float, nullable=False)
    acs_offset_v = db.Column(db.Float, nullable=False)
    rssi = db.Column(db.Integer, nullable=False)
    fault_flags = db.Column(db.Integer, nullable=False)


class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    event_type = db.Column(db.String(64), nullable=False)
    data_json = db.Column(db.Text, nullable=True)
    __table_args__ = (db.Index("idx_events_device_ts", "device_id", "ts"),)


class Command(db.Model):
    __tablename__ = "commands"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    cmd = db.Column(db.String(64), nullable=False)
    args_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent = db.Column(db.Boolean, default=False, nullable=False)
    acknowledged = db.Column(db.Boolean, default=False, nullable=False)
    acknowledged_at = db.Column(db.DateTime, nullable=True)


class DeviceSettings(db.Model):
    __tablename__ = "device_settings"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False, unique=True)
    mode = db.Column(db.String(32), nullable=False, default="auto")
    min_net_gain_wh = db.Column(db.Float, nullable=True)
    max_moves_per_hour = db.Column(db.Integer, nullable=True)
    motor_power_w = db.Column(db.Float, nullable=True)
    hold_power_w = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CleaningLog(db.Model):
    __tablename__ = "cleaning_logs"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    cleaned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    cleaning_type = db.Column(db.String(32), nullable=False)
    note = db.Column(db.Text, nullable=True)
    energy_before_wh = db.Column(db.Float, nullable=True)
    energy_after_wh = db.Column(db.Float, nullable=True)
    improvement_percent = db.Column(db.Float, nullable=True)


class SlotStatistic(db.Model):
    __tablename__ = "slot_statistics"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    slot = db.Column(db.Integer, nullable=False, index=True)
    angle_deg = db.Column(db.Float, nullable=False)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    avg_power = db.Column(db.Float, nullable=False, default=0.0)
    std_power = db.Column(db.Float, nullable=False, default=0.0)
    last_updated = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailySummary(db.Model):
    __tablename__ = "daily_summary"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    energy_wh = db.Column(db.Float, nullable=False, default=0.0)
    move_count = db.Column(db.Integer, nullable=False, default=0)
    efficiency_ratio = db.Column(db.Float, nullable=True)


class MovementLog(db.Model):
    __tablename__ = "movement_logs"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    from_angle = db.Column(db.Float, nullable=True)
    to_angle = db.Column(db.Float, nullable=True)
    move_duration_sec = db.Column(db.Float, nullable=True)
    motor_estimated_power_w = db.Column(db.Float, nullable=True)
    energy_cost_wh = db.Column(db.Float, nullable=True)
    triggered_by = db.Column(db.String(32), nullable=True)


class AISummary(db.Model):
    __tablename__ = "ai_summary"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    summary_json = db.Column(db.Text, nullable=False)


class Alert(db.Model):
    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    severity = db.Column(db.String(16), nullable=False, default="info")
    title = db.Column(db.String(255), nullable=False)
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    cleared = db.Column(db.Boolean, nullable=False, default=False)


class DeviceFaultLog(db.Model):
    __tablename__ = "device_fault_logs"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), db.ForeignKey("devices.device_id"), index=True, nullable=False)
    ts = db.Column(db.DateTime, nullable=False, index=True)
    fault_type = db.Column(db.String(64), nullable=False, index=True)
    details_json = db.Column(db.Text, nullable=True)
    correlated_move_id = db.Column(db.Integer, db.ForeignKey("movement_logs.id"), nullable=True)
    severity = db.Column(db.String(16), nullable=False, default="info")
    __table_args__ = (db.Index("idx_fault_device_type", "device_id", "fault_type", "ts"),)
