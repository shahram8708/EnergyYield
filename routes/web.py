from datetime import datetime, timedelta, timezone
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from auth import get_current_user, login_required, require_ownership
from database import db
from models import CleaningLog, Device, Telemetry, User

web_bp = Blueprint("web", __name__)

ONLINE_WINDOW = timedelta(minutes=2)


def _get_device_or_404(device_id: str) -> Device:
    device = Device.query.filter_by(device_id=device_id).first()
    if not device:
        abort(404, description="Device not found")
    return device


def _is_online(last_seen):
    if not last_seen:
        return False
    now = datetime.now(timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return now - last_seen <= ONLINE_WINDOW


def _get_user_devices(user):
    return Device.query.filter_by(user_id=user.id).order_by(Device.device_id.asc()).all()


def _get_selected_device_id(user):
    selected = session.get("selected_device_id")
    if selected:
        device = Device.query.filter_by(user_id=user.id, device_id=selected).first()
        if device:
            return selected
    first = Device.query.filter_by(user_id=user.id).order_by(Device.device_id.asc()).first()
    if first:
        session["selected_device_id"] = first.device_id
        return first.device_id
    return None


@web_bp.before_request
def load_user():
    get_current_user()


@web_bp.app_context_processor
def inject_nav_devices():
    user = get_current_user()
    if not user:
        return {}
    devices = _get_user_devices(user)
    selected = _get_selected_device_id(user)
    return {"nav_devices": devices, "nav_selected_device_id": selected}


@web_bp.route("/")
@login_required
def dashboard_redirect():
    return redirect(url_for("web.dashboard"))


@web_bp.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    return render_template("dashboard.html", devices=devices, selected_device_id=selected_device_id)


@web_bp.route("/history")
@login_required
def history():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    today = datetime.now(timezone.utc).date().isoformat()
    return render_template("history.html", devices=devices, selected_device_id=selected_device_id, default_date=today)


@web_bp.route("/control")
@login_required
def control():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    return render_template("control.html", devices=devices, selected_device_id=selected_device_id)


@web_bp.route("/analytics")
@login_required
def analytics():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    return render_template("analytics.html", devices=devices, selected_device_id=selected_device_id)


@web_bp.route("/ai")
@login_required
def ai_dashboard():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    return render_template("ai_dashboard.html", devices=devices, selected_device_id=selected_device_id)


@web_bp.route("/maintenance")
@login_required
def maintenance():
    user = get_current_user()
    selected_device_id = _get_selected_device_id(user)
    devices = _get_user_devices(user)
    logs = []
    if selected_device_id:
        logs = (
            CleaningLog.query.filter_by(device_id=selected_device_id)
            .order_by(CleaningLog.cleaned_at.desc())
            .limit(100)
            .all()
        )
    return render_template("maintenance.html", devices=devices, selected_device_id=selected_device_id, logs=logs)


@web_bp.route("/devices")
@login_required
def devices_overview():
    user = get_current_user()
    devices = _get_user_devices(user)
    cards = []
    for d in devices:
        latest = Telemetry.query.filter_by(device_id=d.device_id).order_by(Telemetry.ts.desc()).first()
        cards.append(
            {
                "device": d,
                "online": _is_online(d.last_seen),
                "latest_energy": latest.e_wh_today if latest else None,
            }
        )
    return render_template("devices_overview.html", devices=cards)


@web_bp.route("/device/<device_id>")
@login_required
def device_detail(device_id: str):
    device = _get_device_or_404(device_id)
    require_ownership(device)
    session["selected_device_id"] = device.device_id
    latest = (
        Telemetry.query.filter_by(device_id=device.device_id)
        .order_by(Telemetry.ts.desc())
        .first()
    )
    return render_template("device_detail.html", device=device, latest=latest)


@web_bp.route("/device/<device_id>/settings", methods=["GET", "POST"])
@login_required
def device_settings(device_id: str):
    device = _get_device_or_404(device_id)
    require_ownership(device)
    if request.method == "POST":
        new_key = secrets.token_hex(32)
        device.api_key = new_key
        db.session.commit()
        flash("API key regenerated. Update your firmware with the new key.", "info")
        return render_template("device_settings.html", device=device, api_key=new_key)

    masked_key = None
    if device.api_key:
        masked_key = f"***{device.api_key[-6:]}"
    return render_template("device_settings.html", device=device, masked_key=masked_key)


@web_bp.route("/claim_device", methods=["GET", "POST"])
@login_required
def claim_device():
    user = get_current_user()
    api_key = None
    claimed_device = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        device_id = payload.get("device_id", "").strip()
        if not device_id:
            flash("Device ID is required", "danger")
            return redirect(url_for("web.claim_device"))

        device = Device.query.filter_by(device_id=device_id).first()
        if device and device.user_id and device.user_id != user.id:
            flash("Device already claimed by another user", "danger")
            return redirect(url_for("web.claim_device"))

        if not device:
            device = Device(device_id=device_id, name=device_id, registered_at=datetime.utcnow())
            db.session.add(device)
            db.session.flush()

        device.user_id = user.id
        api_key = secrets.token_hex(32)
        device.api_key = api_key
        db.session.commit()
        claimed_device = device
        session["selected_device_id"] = device.device_id
        flash("Device claimed. Copy the API key now.", "success")

    unassigned = Device.query.filter(Device.user_id.is_(None)).order_by(Device.device_id.asc()).all()
    return render_template(
        "claim_device.html",
        unassigned=unassigned,
        api_key=api_key,
        device=claimed_device,
    )


@web_bp.route("/api-test")
@login_required
def api_test():
    sample = {
        "type": "telemetry",
        "device_id": "AEY-001",
        "seq": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "slot": 1,
        "v_panel": 0.0,
        "i_panel": 0.0,
        "p_w": 0.0,
        "e_wh_today": 0.0,
        "angle_deg": 0.0,
        "mode": "auto",
        "move_count_today": 0,
        "v_sys_5v": 0.0,
        "acs_offset_v": 0.0,
        "rssi": -70,
        "fault_flags": 0,
    }
    return render_template("api_test.html", sample_json=sample)


@web_bp.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user():
        return redirect(url_for("web.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            flash("All fields are required", "danger")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return render_template("register.html")

        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session["user_id"] = user.id
        session.permanent = True
        flash("Registration successful", "success")
        return redirect(url_for("web.dashboard"))

    return render_template("register.html")


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("web.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid credentials", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user.id
        session.permanent = True
        _get_selected_device_id(user)
        flash("Logged in", "success")
        return redirect(url_for("web.dashboard"))

    return render_template("login.html")


@web_bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("web.login"))
