import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(os.path.dirname(__file__), 'data.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME_SECONDS", 86400))

    # Data source selection: SIMULATOR or DEVICE
    DATA_SOURCE = os.environ.get("DATA_SOURCE", "SIMULATOR").upper()
    # Simulator tuning
    SIM_SPEED = os.environ.get("SIM_SPEED", "FAST").upper()
    SIMULATOR_DEVICE_ID = os.environ.get("SIMULATOR_DEVICE_ID", "AEY-SIM-001")
    SIMULATOR_API_KEY = os.environ.get("SIMULATOR_API_KEY", "SIM-LOCAL-KEY")
