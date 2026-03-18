import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "config.db"
VERSION_FILE = BASE_DIR / "VERSION"
DEFAULT_SECRET = "change-me-skyjson-secret"
APP_VERSION = "1.8.6"
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
        conn.commit()


init_db()


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
    return "url" if source_type == "url" else "file"


def replace_filename_in_url(url: str, new_name: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    path = parts.path or ""
    if "/" in path:
        base_dir, _, _ = path.rpartition("/")
        new_path = f"{base_dir}/{new_name}" if base_dir else f"/{new_name}"
    else:
        new_path = f"/{new_name}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def derive_receiver_path(aircraft_path: str) -> str:
    if not aircraft_path:
        return ""
    path = Path(aircraft_path)
    return str(path.with_name("receiver.json"))


def derive_receiver_url(aircraft_url: str) -> str:
    return replace_filename_in_url(aircraft_url, "receiver.json")


def get_source_settings() -> Dict[str, str]:
    source_type = normalize_source_type(get_setting("source_type", "file"))
    aircraft_path = get_setting("aircraft_path", "") or ""
    aircraft_url = get_setting("aircraft_url", "") or ""
    receiver_path = (get_setting("receiver_path", "") or "").strip() or derive_receiver_path(aircraft_path)
    receiver_url = (get_setting("receiver_url", "") or "").strip() or derive_receiver_url(aircraft_url)
    return {
        "source_type": source_type,
        "aircraft_path": aircraft_path,
        "aircraft_url": aircraft_url,
        "receiver_path": receiver_path,
        "receiver_url": receiver_url,
    }


def read_payload_from_file(file_path: str, empty_error: str, missing_prefix: str) -> Dict[str, Any]:
    if not file_path:
        return {"error": empty_error}
    path = Path(file_path)
    if not path.exists():
        return {"error": f"{missing_prefix}: {file_path}"}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {"error": f"Failed to read local JSON data: {exc}"}


def read_payload_from_url(url: str, empty_error: str) -> Dict[str, Any]:
    if not url:
        return {"error": empty_error}
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"error": f"Failed to fetch remote JSON data: {exc}"}
    except ValueError as exc:
        return {"error": f"Remote source did not return valid JSON: {exc}"}


REGISTRATION_PREFIX_FLAGS = [
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
    reg = (registration or "").strip().upper()
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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_km * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def load_receiver() -> Optional[Dict[str, Any]]:
    settings = get_source_settings()
    if settings["source_type"] == "url":
        payload = read_payload_from_url(settings["receiver_url"], "No remote receiver.json URL is configured yet.")
    else:
        payload = read_payload_from_file(
            settings["receiver_path"],
            "No local receiver.json path is configured yet.",
            "Configured receiver file was not found",
        )

    if payload.get("error"):
        return None

    lat = safe_float(payload.get("lat"))
    lon = safe_float(payload.get("lon"))
    if lat is None or lon is None:
        return None

    return {
        "lat": lat,
        "lon": lon,
        "refresh": safe_int(payload.get("refresh")),
        "history": safe_int(payload.get("history")),
    }


def load_aircraft() -> Dict[str, Any]:
    settings = get_source_settings()
    if settings["source_type"] == "url":
        payload = read_payload_from_url(settings["aircraft_url"], "No remote aircraft.json URL is configured yet.")
    else:
        payload = read_payload_from_file(
            settings["aircraft_path"],
            "No local aircraft.json path is configured yet.",
            "Configured aircraft file was not found",
        )

    receiver = load_receiver()

    if payload.get("error"):
        return {"error": payload.get("error"), "aircraft": [], "receiver": receiver, "now": None}

    aircraft = []
    for item in payload.get("aircraft", []):
        country_info = get_country_info(item.get("hex", "-"), item.get("r", ""))
        lat = safe_float(item.get("lat"))
        lon = safe_float(item.get("lon"))
        distance_km = None
        if receiver and lat is not None and lon is not None:
            distance_km = round(haversine_km(receiver["lat"], receiver["lon"], lat, lon), 1)
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
                "distance_km": distance_km,
                "country": country_info["country"],
                "flag": country_info["flag"],
            }
        )

    aircraft.sort(key=lambda x: ((x["flight"] or x["registration"] or x["hex"]).lower(), x["hex"]))
    return {"error": None, "aircraft": aircraft, "receiver": receiver, "now": payload.get("now")}


def summarize_aircraft(aircraft: List[Dict[str, Any]]) -> Dict[str, Any]:
    visible_positions = [a for a in aircraft if a["lat"] is not None and a["lon"] is not None]
    altitudes = [a["alt_baro"] for a in aircraft if a["alt_baro"] is not None]
    speeds = [a["gs"] for a in aircraft if a["gs"] is not None]
    distances = [a["distance_km"] for a in aircraft if a.get("distance_km") is not None]
    return {
        "total": len(aircraft),
        "with_position": len(visible_positions),
        "avg_altitude": round(sum(altitudes) / len(altitudes)) if altitudes else None,
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "nearest": min(distances) if distances else None,
    }


def filter_aircraft(aircraft: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    if not query:
        return aircraft
    q = query.strip().lower()
    return [
        a for a in aircraft
        if q in (a["hex"] or "").lower()
        or q in (a["flight"] or "").lower()
        or q in (a["registration"] or "").lower()
        or q in (a["type"] or "").lower()
        or q in (a["country"] or "").lower()
        or q in (a["signal_source"] or "").lower()
    ]


def map_payload_from_aircraft(aircraft: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
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
            "distance_km": a.get("distance_km"),
        }
        for a in aircraft
        if a["lat"] is not None and a["lon"] is not None
    ]


@app.route("/")
@require_installation
def index():
    data = load_aircraft()
    aircraft = filter_aircraft(data["aircraft"], request.args.get("q") or "")
    stats = summarize_aircraft(aircraft)
    return render_template(
        "dashboard.html",
        aircraft=aircraft,
        map_aircraft=map_payload_from_aircraft(aircraft),
        receiver=data.get("receiver"),
        total_results=len(aircraft),
        stats=stats,
        error=data["error"],
        query=(request.args.get("q") or "").strip().lower(),
        refresh_interval=1,
    )


@app.route("/api/aircraft")
@require_installation
def api_aircraft():
    data = load_aircraft()
    aircraft = filter_aircraft(data["aircraft"], request.args.get("q") or "")
    return {
        "error": data.get("error"),
        "stats": summarize_aircraft(aircraft),
        "total_results": len(aircraft),
        "aircraft": aircraft,
        "map_aircraft": map_payload_from_aircraft(aircraft),
        "receiver": data.get("receiver"),
    }


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_installed():
        return redirect(url_for("index"))

    if request.method == "POST":
        source_type = normalize_source_type(request.form.get("source_type"))
        aircraft_path = (request.form.get("aircraft_path") or "").strip()
        aircraft_url = (request.form.get("aircraft_url") or "").strip()
        receiver_path = (request.form.get("receiver_path") or "").strip()
        receiver_url = (request.form.get("receiver_url") or "").strip()
        enable_auth = request.form.get("enable_auth") == "on"
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if source_type == "file" and not aircraft_path:
            flash("Please enter a local path to aircraft.json.", "danger")
            return render_template("setup.html")
        if source_type == "url" and not aircraft_url:
            flash("Please enter a remote URL to aircraft.json.", "danger")
            return render_template("setup.html")
        if enable_auth and (not username or not password):
            flash("When configuration protection is enabled, a username and password are required.", "danger")
            return render_template("setup.html")

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

    return render_template("setup.html")


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
    if request.method == "POST":
        source_type = normalize_source_type(request.form.get("source_type"))
        aircraft_path = (request.form.get("aircraft_path") or "").strip()
        aircraft_url = (request.form.get("aircraft_url") or "").strip()
        receiver_path = (request.form.get("receiver_path") or "").strip()
        receiver_url = (request.form.get("receiver_url") or "").strip()

        if source_type == "file" and not aircraft_path:
            flash("Please enter a local path to aircraft.json.", "danger")
            return redirect(url_for("config"))
        if source_type == "url" and not aircraft_url:
            flash("Please enter a remote URL to aircraft.json.", "danger")
            return redirect(url_for("config"))

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

    versions = get_versions_simple()
    settings = {
        "source_type": get_setting("source_type", "file"),
        "aircraft_path": get_setting("aircraft_path", ""),
        "aircraft_url": get_setting("aircraft_url", ""),
        "receiver_path": get_setting("receiver_path", ""),
        "receiver_url": get_setting("receiver_url", ""),
        "config_username": get_setting("config_username", ""),
    }
    return render_template("config.html", versions=versions, settings=settings)


@app.route("/config/update", methods=["POST"])
@config_login_required
def update_app():
    pull = run_cmd(["git", "pull", "--ff-only"])
    if pull["ok"]:
        flash("SkyJSON was updated successfully. Restart the application to load the new version.", "success")
    else:
        flash(f"Update failed: {pull['stderr'] or pull['stdout']}", "danger")
    return redirect(url_for("config"))


@app.route("/config/restart", methods=["POST"])
@config_login_required
def restart_app():
    def delayed_restart_process() -> None:
        python_executable = sys.executable
        argv = [python_executable] + sys.argv
        quoted_cmd = " ".join(shlex.quote(part) for part in argv)
        restart_script = f"sleep 2; cd {shlex.quote(str(BASE_DIR))} && exec {quoted_cmd}"
        subprocess.Popen(
            ["/bin/sh", "-c", restart_script],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=delayed_restart_process, daemon=True).start()
    return render_template("restart.html")


@app.route("/health")
def health():
    versions = get_versions_simple()
    return {
        "status": "ok",
        "app": "SkyJSON",
        "server_version": versions["local"],
        "github_version": versions["remote"],
    }


if __name__ == "__main__":
    host = os.environ.get("SKYJSON_HOST", "0.0.0.0")
    port = int(os.environ.get("SKYJSON_PORT", "8000"))
    debug = os.environ.get("SKYJSON_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, use_reloader=False)
