import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "config.db"
VERSION_FILE = BASE_DIR / "VERSION"
DEFAULT_SECRET = "change-me-skyjson-secret"
APP_VERSION = "1.8.7"
REQUEST_TIMEOUT = 10
GITHUB_SPONSOR_URL = "https://github.com/sponsors/PatrickS86"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SKYJSON_SECRET_KEY", DEFAULT_SECRET)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS polar_points (
                receiver_key TEXT NOT NULL,
                bearing_bucket INTEGER NOT NULL,
                distance_km REAL NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (receiver_key, bearing_bucket)
            )
            """
        )
        conn.commit()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


init_db()


def run_cmd(args: List[str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.returncode,
        }
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "code": -1}


def read_version_from_text(text: str) -> Optional[str]:
    match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def read_version_from_path(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if path.name == "VERSION":
        return content or None
    return read_version_from_text(content)


def get_local_file_version() -> str:
    return read_version_from_path(VERSION_FILE) or read_version_from_path(BASE_DIR / "app.py") or APP_VERSION


def get_remote_version() -> Optional[str]:
    if not (BASE_DIR / ".git").exists():
        return None

    fetch = run_cmd(["git", "fetch", "--tags", "origin"])
    if not fetch["ok"]:
        return None

    upstream = run_cmd(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if not upstream["ok"] or not upstream["stdout"]:
        return None

    show_version = run_cmd(["git", "show", f"{upstream['stdout']}:VERSION"])
    if show_version["ok"] and show_version["stdout"]:
        return show_version["stdout"].strip()

    show_app = run_cmd(["git", "show", f"{upstream['stdout']}:app.py"])
    if show_app["ok"] and show_app["stdout"]:
        return read_version_from_text(show_app["stdout"])

    return None


def get_versions_simple() -> Dict[str, Any]:
    local_version = get_local_file_version()
    remote_version = get_remote_version() or "unknown"
    update_available = remote_version not in ("unknown", "", None) and remote_version != local_version
    return {
        "local": local_version,
        "remote": remote_version,
        "update_available": update_available,
        "status_text": "Update available" if update_available else "No new updates",
    }


def is_installed() -> bool:
    return get_setting("installed", "0") == "1"


def config_auth_enabled() -> bool:
    return get_setting("config_auth_enabled", "0") == "1"


def is_logged_in() -> bool:
    return session.get("config_logged_in", False) is True


def require_installation(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_installed() and request.endpoint not in {"setup", "static"}:
            return redirect(url_for("setup"))
        return fn(*args, **kwargs)
    return wrapper


def config_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_installed():
            return redirect(url_for("setup"))
        if config_auth_enabled() and not is_logged_in():
            return redirect(url_for("config_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def get_github_donation_url() -> str:
    return GITHUB_SPONSOR_URL


@app.context_processor
def inject_globals():
    return {
        "app_title": "SkyJSON",
        "app_version": get_local_file_version(),
        "config_auth_enabled": config_auth_enabled(),
        "is_logged_in": is_logged_in(),
        "github_donation_url": get_github_donation_url(),
    }


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_source_type(source_type: Optional[str]) -> str:
    if source_type in {"url", "remote"}:
        return "url"
    return "file"


def get_source_settings() -> Dict[str, str]:
    return {
        "source_type": normalize_source_type(get_setting("source_type", "file")),
        "aircraft_path": get_setting("aircraft_path", "") or "",
        "aircraft_url": get_setting("aircraft_url", "") or "",
        "receiver_path": get_setting("receiver_path", "") or "",
        "receiver_url": get_setting("receiver_url", "") or "",
        "config_username": get_setting("config_username", "") or "",
    }


def read_payload_from_file(file_path: str) -> Dict[str, Any]:
    if not file_path:
        return {"error": "No local JSON path is configured yet."}
    path = Path(file_path)
    if not path.exists():
        return {"error": f"Configured file was not found: {file_path}"}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {"error": f"Failed to read local JSON data: {exc}"}


def read_payload_from_url(url: str) -> Dict[str, Any]:
    if not url:
        return {"error": "No remote JSON URL is configured yet."}
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"error": f"Failed to fetch remote JSON data: {exc}"}
    except ValueError as exc:
        return {"error": f"Remote source did not return valid JSON: {exc}"}


def read_receiver() -> Dict[str, Any]:
    settings = get_source_settings()
    payload: Dict[str, Any] = {}
    if settings["source_type"] == "url" and settings["receiver_url"]:
        payload = read_payload_from_url(settings["receiver_url"])
    elif settings["source_type"] == "file" and settings["receiver_path"]:
        payload = read_payload_from_file(settings["receiver_path"])
    else:
        return {"lat": None, "lon": None}

    if payload.get("error"):
        return {"lat": None, "lon": None}

    return {
        "lat": safe_float(payload.get("lat")),
        "lon": safe_float(payload.get("lon")),
    }


REGISTRATION_PREFIX_FLAGS = [
    ("4X-", "Israel", "🇮🇱"),
    ("4X", "Israel", "🇮🇱"),
    ("9H-", "Malta", "🇲🇹"),
    ("9H", "Malta", "🇲🇹"),
    ("PH-", "Netherlands", "🇳🇱"),
    ("OO-", "Belgium", "🇧🇪"),
    ("D-", "Germany", "🇩🇪"),
    ("F-", "France", "🇫🇷"),
    ("G-", "United Kingdom", "🇬🇧"),
    ("N", "United States", "🇺🇸"),
    ("C-", "Canada", "🇨🇦"),
]

HEX_PREFIX_FLAGS = [
    ("48", "Netherlands", "🇳🇱"),
    ("44", "United Kingdom", "🇬🇧"),
]


def get_country_info(hex_code: str, registration: str) -> Dict[str, str]:
    reg = (registration or "").strip().upper().replace(" ", "")
    hex_upper = (hex_code or "").strip().upper()

    for prefix, country, flag in REGISTRATION_PREFIX_FLAGS:
        if reg.startswith(prefix):
            return {"country": country, "flag": flag}

    for prefix, country, flag in HEX_PREFIX_FLAGS:
        if hex_upper.startswith(prefix):
            return {"country": country, "flag": flag}

    return {"country": "Unknown", "flag": "🏳️"}


def detect_signal_source(item: Dict[str, Any]) -> str:
    source_raw = str(item.get("type") or item.get("dbFlags") or "").strip().lower()
    if "mlat" in source_raw:
        return "MLAT"
    if "mode_s" in source_raw or "mode-s" in source_raw or source_raw == "modes":
        return "Mode-S"
    if "adsb" in source_raw or "ads-b" in source_raw:
        return "ADS-B"
    if item.get("lat") is not None and item.get("lon") is not None:
        return "ADS-B"
    return "Unknown"


def haversine_km(lat1: Optional[float], lon1: Optional[float], lat2: Optional[float], lon2: Optional[float]) -> Optional[float]:
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def bearing_degrees(lat1: Optional[float], lon1: Optional[float], lat2: Optional[float], lon2: Optional[float]) -> Optional[float]:
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    brng = math.degrees(math.atan2(y, x))
    return (brng + 360.0) % 360.0


def receiver_key(receiver: Dict[str, Any]) -> Optional[str]:
    lat = receiver.get("lat")
    lon = receiver.get("lon")
    if lat is None or lon is None:
        return None
    return f"{round(lat, 4)}:{round(lon, 4)}"


def update_polar_points(receiver: Dict[str, Any], aircraft: List[Dict[str, Any]]) -> None:
    key = receiver_key(receiver)
    if not key:
        return
    now_ts = int(time.time())
    with get_db() as conn:
        for a in aircraft:
            if a["lat"] is None or a["lon"] is None or a["dist_km"] is None:
                continue
            bearing = bearing_degrees(receiver["lat"], receiver["lon"], a["lat"], a["lon"])
            if bearing is None:
                continue
            bucket = int(round(bearing)) % 360
            row = conn.execute(
                "SELECT distance_km FROM polar_points WHERE receiver_key = ? AND bearing_bucket = ?",
                (key, bucket),
            ).fetchone()
            new_dist = float(a["dist_km"])
            if row is None or new_dist > float(row["distance_km"]):
                conn.execute(
                    """
                    INSERT INTO polar_points (receiver_key, bearing_bucket, distance_km, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(receiver_key, bearing_bucket)
                    DO UPDATE SET distance_km = excluded.distance_km, updated_at = excluded.updated_at
                    """,
                    (key, bucket, new_dist, now_ts),
                )
        conn.commit()


def get_polar_points(receiver: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = receiver_key(receiver)
    if not key:
        return []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT bearing_bucket, distance_km
            FROM polar_points
            WHERE receiver_key = ?
            ORDER BY bearing_bucket ASC
            """,
            (key,),
        ).fetchall()
    return [{"bearing": int(r["bearing_bucket"]), "distance_km": float(r["distance_km"])} for r in rows]


def load_aircraft() -> Dict[str, Any]:
    settings = get_source_settings()
    if settings["source_type"] == "url":
        payload = read_payload_from_url(settings["aircraft_url"])
    else:
        payload = read_payload_from_file(settings["aircraft_path"])

    if payload.get("error"):
        return {
            "error": payload["error"],
            "aircraft": [],
            "now": None,
            "receiver": {"lat": None, "lon": None},
            "polar_points": [],
        }

    receiver = read_receiver()
    aircraft: List[Dict[str, Any]] = []
    for item in payload.get("aircraft", []):
        lat = safe_float(item.get("lat"))
        lon = safe_float(item.get("lon"))
        country_info = get_country_info(item.get("hex", "-"), item.get("r", ""))
        aircraft.append(
            {
                "hex": item.get("hex", "-"),
                "flight": (item.get("flight") or "").strip(),
                "registration": item.get("r", ""),
                "type": item.get("t", ""),
                "signal_source": detect_signal_source(item),
                "alt_baro": safe_int(item.get("alt_baro")),
                "gs": safe_float(item.get("gs")),
                "track": safe_float(item.get("track")),
                "lat": lat,
                "lon": lon,
                "messages": safe_int(item.get("messages")),
                "country": country_info["country"],
                "flag": country_info["flag"],
                "dist_km": haversine_km(receiver["lat"], receiver["lon"], lat, lon),
            }
        )

    update_polar_points(receiver, aircraft)
    polar_points = get_polar_points(receiver)

    aircraft.sort(key=lambda x: ((x["flight"] or x["registration"] or x["hex"]).lower(), x["hex"]))
    return {
        "error": None,
        "aircraft": aircraft,
        "now": payload.get("now"),
        "receiver": receiver,
        "polar_points": polar_points,
    }


def summarize_aircraft(aircraft: List[Dict[str, Any]]) -> Dict[str, Any]:
    visible_positions = [a for a in aircraft if a["lat"] is not None and a["lon"] is not None]
    altitudes = [a["alt_baro"] for a in aircraft if a["alt_baro"] is not None]
    speeds = [a["gs"] for a in aircraft if a["gs"] is not None]
    return {
        "total": len(aircraft),
        "with_position": len(visible_positions),
        "avg_altitude": round(sum(altitudes) / len(altitudes)) if altitudes else None,
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else None,
    }


@app.route("/")
@require_installation
def index():
    data = load_aircraft()
    aircraft = data["aircraft"]
    stats = summarize_aircraft(aircraft)
    query = (request.args.get("q") or "").strip().lower()

    if query:
        aircraft = [
            a for a in aircraft
            if query in (a["hex"] or "").lower()
            or query in (a["flight"] or "").lower()
            or query in (a["registration"] or "").lower()
            or query in (a["type"] or "").lower()
            or query in (a["country"] or "").lower()
            or query in (a["signal_source"] or "").lower()
        ]

    map_aircraft = [
        {
            "hex": a["hex"],
            "flight": a["flight"],
            "registration": a["registration"],
            "type": a["type"],
            "signal_source": a["signal_source"],
            "alt_baro": a["alt_baro"],
            "gs": a["gs"],
            "track": a["track"],
            "lat": a["lat"],
            "lon": a["lon"],
            "country": a["country"],
            "flag": a["flag"],
            "dist_km": a["dist_km"],
        }
        for a in aircraft
        if a["lat"] is not None and a["lon"] is not None
    ]

    return render_template(
        "dashboard.html",
        aircraft=aircraft,
        map_aircraft=map_aircraft,
        polar_points=data.get("polar_points", []),
        total_results=len(aircraft),
        stats=stats,
        error=data["error"],
        query=query,
        refresh_interval=1,
        receiver=data.get("receiver", {"lat": None, "lon": None}),
    )


@app.route("/api/aircraft")
@require_installation
def api_aircraft():
    data = load_aircraft()
    aircraft = data["aircraft"]
    query = (request.args.get("q") or "").strip().lower()

    if query:
        aircraft = [
            a for a in aircraft
            if query in (a["hex"] or "").lower()
            or query in (a["flight"] or "").lower()
            or query in (a["registration"] or "").lower()
            or query in (a["type"] or "").lower()
            or query in (a["country"] or "").lower()
            or query in (a["signal_source"] or "").lower()
        ]

    return {
        "error": data.get("error"),
        "stats": summarize_aircraft(aircraft),
        "total_results": len(aircraft),
        "receiver": data.get("receiver", {"lat": None, "lon": None}),
        "polar_points": data.get("polar_points", []),
        "aircraft": aircraft,
        "map_aircraft": [
            {
                "hex": a["hex"],
                "flight": a["flight"],
                "registration": a["registration"],
                "type": a["type"],
                "signal_source": a["signal_source"],
                "alt_baro": a["alt_baro"],
                "gs": a["gs"],
                "track": a["track"],
                "lat": a["lat"],
                "lon": a["lon"],
                "country": a["country"],
                "flag": a["flag"],
                "dist_km": a["dist_km"],
            }
            for a in aircraft
            if a["lat"] is not None and a["lon"] is not None
        ],
    }


def default_settings_context() -> Dict[str, str]:
    return get_source_settings()


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_installed():
        return redirect(url_for("index"))

    settings = default_settings_context()

    if request.method == "POST":
        source_type = normalize_source_type(request.form.get("source_type"))
        aircraft_path = (request.form.get("aircraft_path") or "").strip()
        aircraft_url = (request.form.get("aircraft_url") or "").strip()
        receiver_path = (request.form.get("receiver_path") or "").strip()
        receiver_url = (request.form.get("receiver_url") or "").strip()
        enable_auth = request.form.get("enable_auth") == "on"
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        settings.update(
            {
                "source_type": source_type,
                "aircraft_path": aircraft_path,
                "aircraft_url": aircraft_url,
                "receiver_path": receiver_path,
                "receiver_url": receiver_url,
                "config_username": username,
            }
        )

        if source_type == "file" and not aircraft_path:
            flash("Please enter a local path to aircraft.json.", "danger")
            return render_template("setup.html", settings=settings)
        if source_type == "url" and not aircraft_url:
            flash("Please enter a remote URL to aircraft.json.", "danger")
            return render_template("setup.html", settings=settings)
        if enable_auth and (not username or not password):
            flash("When configuration protection is enabled, a username and password are required.", "danger")
            return render_template("setup.html", settings=settings)

        set_setting("source_type", source_type)
        set_setting("aircraft_path", aircraft_path)
        set_setting("aircraft_url", aircraft_url)
        set_setting("receiver_path", receiver_path)
        set_setting("receiver_url", receiver_url)
        set_setting("config_auth_enabled", "1" if enable_auth else "0")
        if enable_auth:
            set_setting("config_username", username)
            set_setting("config_password_hash", generate_password_hash(password))
        set_setting("installed", "1")
        flash("SkyJSON has been configured successfully.", "success")
        return redirect(url_for("index"))

    return render_template("setup.html", settings=settings)


@app.route("/config/login", methods=["GET", "POST"])
def config_login():
    if not config_auth_enabled():
        return redirect(url_for("config"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        stored_username = get_setting("config_username", "")
        stored_hash = get_setting("config_password_hash", "")
        if username == stored_username and stored_hash and check_password_hash(stored_hash, password):
            session["config_logged_in"] = True
            flash("Logged in successfully.", "success")
            return redirect(request.args.get("next") or url_for("config"))
        flash("Invalid credentials.", "danger")

    return render_template("login.html")


@app.route("/config/logout")
def config_logout():
    session.pop("config_logged_in", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/config", methods=["GET", "POST"])
@config_login_required
def config():
    settings = default_settings_context()

    if request.method == "POST":
        source_type = normalize_source_type(request.form.get("source_type"))
        aircraft_path = (request.form.get("aircraft_path") or "").strip()
        aircraft_url = (request.form.get("aircraft_url") or "").strip()
        receiver_path = (request.form.get("receiver_path") or "").strip()
        receiver_url = (request.form.get("receiver_url") or "").strip()

        settings.update(
            {
                "source_type": source_type,
                "aircraft_path": aircraft_path,
                "aircraft_url": aircraft_url,
                "receiver_path": receiver_path,
                "receiver_url": receiver_url,
                "config_username": (request.form.get("username") or "").strip() or settings.get("config_username", ""),
            }
        )

        if source_type == "file" and not aircraft_path:
            flash("Please enter a local path to aircraft.json.", "danger")
            return render_template("config.html", versions=get_versions_simple(), settings=settings)
        if source_type == "url" and not aircraft_url:
            flash("Please enter a remote URL to aircraft.json.", "danger")
            return render_template("config.html", versions=get_versions_simple(), settings=settings)

        set_setting("source_type", source_type)
        set_setting("aircraft_path", aircraft_path)
        set_setting("aircraft_url", aircraft_url)
        set_setting("receiver_path", receiver_path)
        set_setting("receiver_url", receiver_url)

        enable_auth = request.form.get("enable_auth") == "on"
        set_setting("config_auth_enabled", "1" if enable_auth else "0")
        if enable_auth:
            username = (request.form.get("username") or "").strip()
            password = (request.form.get("password") or "").strip()
            if username:
                set_setting("config_username", username)
            if password:
                set_setting("config_password_hash", generate_password_hash(password))

        flash("Configuration saved.", "success")
        return redirect(url_for("config"))

    settings["config_username"] = get_setting("config_username", "") or ""
    return render_template("config.html", versions=get_versions_simple(), settings=settings)


@app.route("/config/update", methods=["POST"])
@config_login_required
def update_app():
    pull = run_cmd(["git", "pull", "--ff-only"])
    if pull["ok"]:
        flash("SkyJSON was updated successfully. Restart the application to load the new version.", "success")
    else:
        flash(f"Update failed: {pull['stderr'] or pull['stdout']}", "danger")
    return redirect(url_for("config"))


def delayed_restart() -> None:
    time.sleep(2)
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.route("/config/restart", methods=["POST"])
@config_login_required
def restart_app():
    thread = threading.Thread(target=delayed_restart, daemon=True)
    thread.start()
    return render_template("restart.html")


@app.route("/health")
def health():
    return {"status": "ok", "version": get_local_file_version()}


if __name__ == "__main__":
    host = os.environ.get("SKYJSON_HOST", "127.0.0.1")
    port = int(os.environ.get("SKYJSON_PORT", "8000"))
    debug = os.environ.get("SKYJSON_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
