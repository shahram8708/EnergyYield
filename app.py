import logging
import os
import threading
import time
from datetime import datetime, timedelta

from flask import Flask

from config import Config
from database import db
from routes.api import api_bp
from routes.web import web_bp
from analytics import run_analytics_cycle
from seed import seed_database
from sqlalchemy import text


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    _configure_logging(app)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _apply_schema_updates()
        seed_database()

    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)

    _start_background_jobs(app)

    return app


def _configure_logging(app: Flask) -> None:
    log_dir = os.path.join(app.root_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "server.log")
    security_log_path = os.path.join(log_dir, "security.log")

    handler = logging.FileHandler(log_path)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)

    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    logging.getLogger("sqlalchemy.engine").addHandler(handler)

    security_handler = logging.FileHandler(security_log_path)
    security_handler.setFormatter(formatter)
    security_handler.setLevel(logging.INFO)
    security_logger = logging.getLogger("security")
    security_logger.setLevel(logging.INFO)
    security_logger.addHandler(security_handler)


def _apply_schema_updates() -> None:
    engine = db.engine
    if engine.dialect.name != "sqlite":
        return
    cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(commands)"))]
    if "acknowledged" not in cols:
        db.session.execute(text("ALTER TABLE commands ADD COLUMN acknowledged BOOLEAN DEFAULT 0"))
    if "acknowledged_at" not in cols:
        db.session.execute(text("ALTER TABLE commands ADD COLUMN acknowledged_at DATETIME"))
    ds_cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(device_settings)"))]
    if "motor_power_w" not in ds_cols:
        db.session.execute(text("ALTER TABLE device_settings ADD COLUMN motor_power_w FLOAT"))
    if "hold_power_w" not in ds_cols:
        db.session.execute(text("ALTER TABLE device_settings ADD COLUMN hold_power_w FLOAT"))
    ai_cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(ai_summary)"))]
    if "explanation_raw" not in ai_cols:
        db.session.execute(text("ALTER TABLE ai_summary ADD COLUMN explanation_raw TEXT"))
    if "explanation_html" not in ai_cols:
        db.session.execute(text("ALTER TABLE ai_summary ADD COLUMN explanation_html TEXT"))
    if "recommendations_html" not in ai_cols:
        db.session.execute(text("ALTER TABLE ai_summary ADD COLUMN recommendations_html TEXT"))
    alert_cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(alerts)"))]
    if "detail_html" not in alert_cols:
        db.session.execute(text("ALTER TABLE alerts ADD COLUMN detail_html TEXT"))
    db.session.commit()


def _process_cleaning_improvements() -> None:
    from models import CleaningLog, Telemetry

    now = datetime.utcnow()
    ready_logs = (
        CleaningLog.query.filter(
            CleaningLog.improvement_percent.is_(None),
            CleaningLog.cleaned_at <= now - timedelta(hours=2),
        ).all()
    )
    for log in ready_logs:
        after_start = log.cleaned_at
        after_end = after_start + timedelta(hours=2)
        avg_after = (
            db.session.query(db.func.avg(Telemetry.p_w))
            .filter(
                Telemetry.device_id == log.device_id,
                Telemetry.ts >= after_start,
                Telemetry.ts < after_end,
            )
            .scalar()
        )
        energy_after_wh = float(avg_after or 0.0) * 2.0
        log.energy_after_wh = energy_after_wh
        before = log.energy_before_wh or 0.0
        if before > 0:
            log.improvement_percent = ((energy_after_wh - before) / before) * 100.0
        else:
            log.improvement_percent = 0.0
    if ready_logs:
        db.session.commit()


def _start_background_jobs(app: Flask) -> None:
    if getattr(app, "_jobs_started", False):
        return

    def worker():
        while True:
            try:
                with app.app_context():
                    _process_cleaning_improvements()
                    run_analytics_cycle()
            except Exception as exc:  # noqa: BLE001
                app.logger.exception("Background job failed: %s", exc)
            time.sleep(900)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    app._jobs_started = True


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
