from functools import wraps
from typing import Callable, Optional

from flask import abort, flash, g, redirect, session, url_for

from models import Device, User


def get_current_user() -> Optional[User]:
    if hasattr(g, "user"):
        return g.user
    user_id = session.get("user_id")
    if not user_id:
        g.user = None
        return None
    user = User.query.get(user_id)
    g.user = user
    return user


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            flash("Please log in to continue", "warning")
            return redirect(url_for("web.login"))
        return view(*args, **kwargs)

    return wrapped


def require_ownership(device: Device) -> None:
    user = get_current_user()
    if not user or device.user_id != user.id:
        abort(403)
