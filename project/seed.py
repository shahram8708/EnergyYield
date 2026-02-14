import math
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Tuple

from werkzeug.security import generate_password_hash

from database import db
from models import Device, DeviceSettings


def _build_app():
    from app import create_app

    return create_app()


def seed_database() -> None:
    """Idempotent bootstrap seeding for local dev and simulator."""
    from models import Device, DeviceSettings, User

    admin_email = os.environ.get("SEED_ADMIN_EMAIL", "admin@example.com")
    admin_password = os.environ.get("SEED_ADMIN_PASSWORD", "admin123")
    admin_name = os.environ.get("SEED_ADMIN_NAME", "Admin")
    seed_device_id = os.environ.get("SIMULATOR_DEVICE_ID", "AEY-SIM-001")
    seed_api_key = os.environ.get("SIMULATOR_API_KEY", "SIM-LOCAL-KEY")

    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            name=admin_name,
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
        )
        db.session.add(admin)
        db.session.flush()

    device = Device.query.filter_by(device_id=seed_device_id).first()
    if not device:
        device = Device(
            device_id=seed_device_id,
            name="Demo Tracker",
            registered_at=datetime.utcnow(),
            api_key=seed_api_key,
            user_id=admin.id,
            is_active=True,
        )
        db.session.add(device)
    else:
        if device.user_id is None:
            device.user_id = admin.id
        if not device.api_key:
            device.api_key = seed_api_key

    settings = DeviceSettings.query.filter_by(device_id=device.device_id).first()
    if not settings:
        settings = DeviceSettings(
            device_id=device.device_id,
            mode="auto",
            min_net_gain_wh=0.0,
            max_moves_per_hour=12,
            motor_power_w=50.0,
            hold_power_w=2.0,
        )
        db.session.add(settings)

    db.session.commit()


class DigitalTwinSimulator:
    def __init__(self, app=None) -> None:
        self.data_source = os.environ.get("DATA_SOURCE", "SIMULATOR").upper()
        if self.data_source != "SIMULATOR":
            raise SystemExit("DATA_SOURCE is DEVICE; simulator is disabled.")

        self.sim_speed = os.environ.get("SIM_SPEED", "FAST").upper()
        self.sim_device_id = os.environ.get("SIMULATOR_DEVICE_ID", "AEY-SIM-001")
        self.sim_api_key = os.environ.get("SIMULATOR_API_KEY", "SIM-LOCAL-KEY")
        self.real_sleep = 1.0 if self.sim_speed == "REALTIME" else (600.0 / 1440.0)

        self.app = app or _build_app()
        self.client = self.app.test_client()

        with self.app.app_context():
            device, settings = self._ensure_device()
            self.motor_power_w = float(settings.motor_power_w or 50.0)
            self.hold_power_w = float(settings.hold_power_w or 2.0)

        self.seq = 1
        self.current_angle = 90.0
        self.e_wh_today = 0.0
        self.move_count_today = 0
        self.dust_factor = 1.0
        self.last_cleaning_day = None
        self.move_times: Deque[datetime] = deque()
        self.start_local = self._now_india()
        self.last_day = self.start_local.date()

    def _now_india(self) -> datetime:
        return datetime.now(timezone(timedelta(hours=5, minutes=30)))

    def _ensure_device(self) -> Tuple[Device, DeviceSettings]:
        device = Device.query.filter_by(device_id=self.sim_device_id).first()
        if not device:
            device = Device(
                device_id=self.sim_device_id,
                name="10W Digital Twin",
                registered_at=datetime.utcnow(),
                is_active=True,
            )
            db.session.add(device)
            db.session.flush()

        if not device.api_key:
            device.api_key = self.sim_api_key
        if device.user_id is not None:
            device.user_id = None
        db.session.commit()

        settings = DeviceSettings.query.filter_by(device_id=device.device_id).first()
        if not settings:
            settings = DeviceSettings(
                device_id=device.device_id,
                mode="auto",
                min_net_gain_wh=0.0,
                max_moves_per_hour=12,
                motor_power_w=50.0,
                hold_power_w=2.0,
            )
            db.session.add(settings)
            db.session.commit()
        return device, settings

    def _cloud_factor(self, day_index: int, minute: int) -> float:
        dips = [
            (600 + (day_index % 12), 8, 0.5),
            (750 + (day_index % 7), 6, 0.35),
            (960 + (day_index % 9), 3, 0.6),
        ]
        factor = 1.0
        for start, duration, depth in dips:
            if start <= minute < start + duration:
                phase = (minute - start) / max(duration, 1)
                factor = min(factor, depth + 0.1 * math.cos(math.pi * phase))
        return max(factor, 0.25)

    def _sun_factor(self, minute: int) -> float:
        sunrise = 6 * 60 + 45
        sunset = 18 * 60 + 30
        if minute < sunrise or minute > sunset:
            return 0.0
        span = sunset - sunrise
        return math.sin(math.pi * (minute - sunrise) / span)

    def _voltage(self, sun_factor: float, cos_loss: float) -> float:
        if sun_factor <= 0.0:
            return 0.8
        base = 18.0 + 1.8 * (sun_factor - 0.5)
        tilt_bonus = 0.4 * cos_loss
        return max(10.5, min(base + tilt_bonus, 21.0))

    def _acs_offset(self, minute: int) -> float:
        return 2.5 + 0.02 * math.sin(2 * math.pi * minute / 1440)

    def _system_voltage(self, p_w: float, moving: bool) -> float:
        rail = 5.08 - 0.04 * (1.0 - min(p_w / 10.0, 1.0))
        if moving:
            rail -= 0.12
        return max(4.6, min(rail, 5.15))

    def _dust_decay(self, day_index: int) -> None:
        daily_loss = 0.02 + 0.03 * ((day_index % 6) / 5.0)
        self.dust_factor = max(0.55, self.dust_factor * (1.0 - daily_loss))

    def _post(self, path: str, payload: Dict) -> None:
        headers = {"X-API-KEY": self.sim_api_key}
        resp = self.client.post(path, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed POST {path}: {resp.status_code} {resp.data}")

    def _send_event(self, ts: datetime, event_type: str, data: Dict) -> None:
        payload = {
            "type": "event",
            "device_id": self.sim_device_id,
            "ts": ts.astimezone(timezone.utc).isoformat(),
            "event_type": event_type,
            "data": data,
        }
        self._post("/api/event", payload)

    def _moves_last_hour(self, current_ts: datetime) -> int:
        while self.move_times and (current_ts - self.move_times[0]).total_seconds() > 3600:
            self.move_times.popleft()
        return len(self.move_times)

    def _maybe_move(self, target_angle: float, ts_local: datetime) -> Tuple[bool, float]:
        if abs(target_angle - self.current_angle) < 2.0:
            return False, 0.0
        moves_hour = self._moves_last_hour(ts_local)
        if moves_hour >= 12:
            return False, 0.0

        duration = 1.2 + abs(target_angle - self.current_angle) / 90.0
        energy_cost = (self.motor_power_w * duration) / 3600.0
        data = {
            "from_angle": round(self.current_angle, 2),
            "to_angle": round(target_angle, 2),
            "move_duration_sec": round(duration, 2),
            "motor_estimated_power_w": round(self.motor_power_w, 2),
            "energy_cost_wh": round(energy_cost, 4),
            "triggered_by": "sun_tracking",
        }
        self._send_event(ts_local, "move", data)
        self.current_angle = target_angle
        self.move_count_today += 1
        self.move_times.append(ts_local)
        self.e_wh_today = max(self.e_wh_today - energy_cost, 0.0)
        return True, energy_cost

    def _maybe_clean(self, ts_local: datetime, day_index: int) -> None:
        minute = ts_local.hour * 60 + ts_local.minute
        if self.dust_factor >= 0.72:
            return
        if minute != 450:
            return
        if self.last_cleaning_day == day_index:
            return
        before = self.dust_factor
        self.dust_factor = 1.0
        self.last_cleaning_day = day_index
        self._send_event(
            ts_local,
            "cleaning",
            {
                "before_efficiency": round(before, 3),
                "after_efficiency": 1.0,
                "method": "manual_rinse",
            },
        )

    def _maybe_reset(self, ts_local: datetime, day_index: int) -> None:
        minute = ts_local.hour * 60 + ts_local.minute
        if day_index % 4 == 0 and minute == 240:
            self._send_event(ts_local, "reset", {"reason": "watchdog_recovery"})

    def _maybe_low_supply(self, ts_local: datetime, v_sys: float) -> None:
        if v_sys < 4.75 and self._moves_last_hour(ts_local) >= 10:
            self._send_event(ts_local, "low_supply", {"v_sys": round(v_sys, 3)})

    def _maybe_sensor_fault(self, ts_local: datetime, sun_factor: float, cloud_factor: float, i_panel: float) -> None:
        if sun_factor > 0.8 and cloud_factor > 0.9 and i_panel < 0.12:
            self._send_event(ts_local, "sensor_fault", {"i_panel": round(i_panel, 3), "note": "zero_current_under_sun"})

    def run(self) -> None:
        ts_local = self.start_local
        while True:
            day_index = (ts_local.date() - self.start_local.date()).days
            if ts_local.date() != self.last_day:
                self._dust_decay(day_index)
                self.e_wh_today = 0.0
                self.move_count_today = 0
                self.last_day = ts_local.date()

            minute = ts_local.hour * 60 + ts_local.minute
            sun_factor = self._sun_factor(minute)
            cloud_factor = self._cloud_factor(day_index, minute)
            solar_alt_deg = sun_factor * 75.0
            optimal_angle = max(30.0, min(150.0, 90.0 - solar_alt_deg))
            cos_loss = max(math.cos(math.radians(abs(self.current_angle - optimal_angle))), 0.0)

            moved, _ = self._maybe_move(optimal_angle, ts_local) if sun_factor > 0 else (False, 0.0)

            irradiance_factor = sun_factor * cloud_factor * self.dust_factor * cos_loss
            base_current = 0.6
            i_panel = base_current * irradiance_factor
            if sun_factor <= 0.0:
                i_panel = 0.005

            v_panel = self._voltage(sun_factor, cos_loss)
            p_w = min(v_panel * i_panel, 10.2 * (sun_factor * cloud_factor + 0.1))
            if sun_factor <= 0.0:
                p_w = min(p_w, 0.08)

            self.e_wh_today += p_w / 60.0
            v_sys = self._system_voltage(p_w, moved)

            telemetry = {
                "type": "telemetry",
                "device_id": self.sim_device_id,
                "seq": self.seq,
                "ts": ts_local.astimezone(timezone.utc).isoformat(),
                "slot": minute,
                "v_panel": round(v_panel, 3),
                "i_panel": round(i_panel, 3),
                "p_w": round(p_w, 3),
                "e_wh_today": round(self.e_wh_today, 3),
                "angle_deg": round(self.current_angle, 2),
                "mode": "auto",
                "move_count_today": self.move_count_today,
                "v_sys_5v": round(v_sys, 3),
                "acs_offset_v": round(self._acs_offset(minute), 3),
                "rssi": -52,
                "fault_flags": 0,
            }

            self._post("/api/telemetry", telemetry)

            self._maybe_low_supply(ts_local, v_sys)
            self._maybe_sensor_fault(ts_local, sun_factor, cloud_factor, i_panel)
            self._maybe_clean(ts_local, day_index)
            self._maybe_reset(ts_local, day_index)

            self.seq += 1
            ts_local += timedelta(minutes=1)
            time.sleep(self.real_sleep)


def main() -> None:
    sim = DigitalTwinSimulator()
    sim.run()


if __name__ == "__main__":
    main()
