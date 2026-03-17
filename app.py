import json
import os
import sqlite3
import subprocess
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'config.db'
DEFAULT_SECRET = 'change-me-skyjson-secret'
APP_VERSION = '1.2.0'
REQUEST_TIMEOUT = 10

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SKYJSON_SECRET_KEY', DEFAULT_SECRET)


# -----------------------------
# Database helpers
# -----------------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            '''
        )
        conn.commit()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )
        conn.commit()


init_db()


# -----------------------------
# App config
# -----------------------------
def is_installed() -> bool:
    return get_setting('installed', '0') == '1'


def config_auth_enabled() -> bool:
    return get_setting('config_auth_enabled', '0') == '1'


def is_logged_in() -> bool:
    return session.get('config_logged_in', False) is True


def require_installation(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_installed() and request.endpoint not in {'setup', 'static'}:
            return redirect(url_for('setup'))
        return fn(*args, **kwargs)

    return wrapper


def config_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_installed():
            return redirect(url_for('setup'))
        if config_auth_enabled() and not is_logged_in():
            return redirect(url_for('config_login', next=request.path))
        return fn(*args, **kwargs)

    return wrapper


@app.context_processor
def inject_globals():
    return {
        'app_title': get_setting('app_title', 'SkyJSON'),
        'app_version': APP_VERSION,
        'config_auth_enabled': config_auth_enabled(),
        'is_logged_in': is_logged_in(),
    }


# -----------------------------
# Aircraft JSON parsing
# -----------------------------
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
    return 'url' if source_type == 'url' else 'file'


def get_source_settings() -> Dict[str, str]:
    source_type = normalize_source_type(get_setting('source_type', 'file'))
    aircraft_path = get_setting('aircraft_path', '') or ''
    aircraft_url = get_setting('aircraft_url', '') or ''
    source_value = aircraft_url if source_type == 'url' else aircraft_path
    return {
        'source_type': source_type,
        'aircraft_path': aircraft_path,
        'aircraft_url': aircraft_url,
        'source_value': source_value,
    }


def read_payload_from_file(file_path: str) -> Dict[str, Any]:
    if not file_path:
        return {'error': 'No local aircraft.json path is configured yet.', 'aircraft': [], 'now': None}

    path = Path(file_path)
    if not path.exists():
        return {'error': f'Configured file was not found: {file_path}', 'aircraft': [], 'now': None}

    try:
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        return {'error': f'Failed to read local aircraft data: {exc}', 'aircraft': [], 'now': None}


def read_payload_from_url(url: str) -> Dict[str, Any]:
    if not url:
        return {'error': 'No remote aircraft.json URL is configured yet.', 'aircraft': [], 'now': None}

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {'error': f'Failed to fetch remote aircraft data: {exc}', 'aircraft': [], 'now': None}
    except ValueError as exc:
        return {'error': f'Remote source did not return valid JSON: {exc}', 'aircraft': [], 'now': None}


def load_aircraft() -> Dict[str, Any]:
    source_settings = get_source_settings()
    source_type = source_settings['source_type']

    if source_type == 'url':
        payload = read_payload_from_url(source_settings['aircraft_url'])
    else:
        payload = read_payload_from_file(source_settings['aircraft_path'])

    if payload.get('error'):
        payload['source_label'] = f"Remote URL: {source_settings['aircraft_url']}" if source_type == 'url' else f"Local file: {source_settings['aircraft_path']}"
        return payload

    items = payload.get('aircraft', [])
    aircraft: List[Dict[str, Any]] = []
    for item in items:
        aircraft.append(
            {
                'hex': item.get('hex', '-'),
                'flight': (item.get('flight') or '').strip(),
                'registration': item.get('r', ''),
                'type': item.get('t', ''),
                'category': item.get('category', ''),
                'squawk': item.get('squawk', ''),
                'alt_baro': safe_int(item.get('alt_baro')),
                'gs': safe_float(item.get('gs')),
                'track': safe_float(item.get('track')),
                'lat': safe_float(item.get('lat')),
                'lon': safe_float(item.get('lon')),
                'seen': safe_float(item.get('seen')),
                'seen_pos': safe_float(item.get('seen_pos')),
                'messages': safe_int(item.get('messages')),
                'rssi': safe_float(item.get('rssi')),
            }
        )

    aircraft.sort(key=lambda x: ((x['flight'] or x['hex']).lower(), x['hex']))
    source_label = f"Remote URL: {source_settings['aircraft_url']}" if source_type == 'url' else f"Local file: {source_settings['aircraft_path']}"
    return {'error': None, 'aircraft': aircraft, 'now': payload.get('now'), 'source_label': source_label}


def summarize_aircraft(aircraft: List[Dict[str, Any]]) -> Dict[str, Any]:
    visible_positions = [a for a in aircraft if a['lat'] is not None and a['lon'] is not None]
    altitudes = [a['alt_baro'] for a in aircraft if a['alt_baro'] is not None]
    speeds = [a['gs'] for a in aircraft if a['gs'] is not None]
    return {
        'total': len(aircraft),
        'with_position': len(visible_positions),
        'avg_altitude': round(sum(altitudes) / len(altitudes)) if altitudes else None,
        'avg_speed': round(sum(speeds) / len(speeds), 1) if speeds else None,
    }


# -----------------------------
# Update helpers
# -----------------------------
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
            'ok': result.returncode == 0,
            'code': result.returncode,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip(),
        }
    except Exception as exc:
        return {'ok': False, 'code': -1, 'stdout': '', 'stderr': str(exc)}


def get_update_status() -> Dict[str, Any]:
    if not (BASE_DIR / '.git').exists():
        return {
            'supported': False,
            'message': 'Updates require SkyJSON to be installed from a Git repository.',
        }

    fetch = run_cmd(['git', 'fetch', 'origin'])
    if not fetch['ok']:
        return {
            'supported': True,
            'update_available': None,
            'message': f"Git fetch failed: {fetch['stderr'] or fetch['stdout']}",
        }

    branch = run_cmd(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    current = run_cmd(['git', 'rev-parse', 'HEAD'])
    remote = run_cmd(['git', 'rev-parse', '@{u}'])

    if not (branch['ok'] and current['ok'] and remote['ok']):
        return {
            'supported': True,
            'update_available': None,
            'message': 'Unable to determine Git update status for this installation.',
        }

    update_available = current['stdout'] != remote['stdout']
    return {
        'supported': True,
        'update_available': update_available,
        'branch': branch['stdout'],
        'current_commit': current['stdout'][:7],
        'remote_commit': remote['stdout'][:7],
        'message': 'A newer version is available on GitHub.' if update_available else 'SkyJSON is up to date.',
    }


# -----------------------------
# Routes
# -----------------------------
@app.route('/')
@require_installation
def index():
    data = load_aircraft()
    aircraft = data['aircraft']
    stats = summarize_aircraft(aircraft)
    query = (request.args.get('q') or '').strip().lower()
    limit = safe_int(get_setting('rows_per_page', '100')) or 100

    if query:
        aircraft = [
            a for a in aircraft
            if query in (a['hex'] or '').lower()
            or query in (a['flight'] or '').lower()
            or query in (a['registration'] or '').lower()
            or query in (a['type'] or '').lower()
        ]

    map_aircraft = [
        {
            'hex': a['hex'],
            'flight': a['flight'],
            'registration': a['registration'],
            'type': a['type'],
            'alt_baro': a['alt_baro'],
            'gs': a['gs'],
            'track': a['track'],
            'lat': a['lat'],
            'lon': a['lon'],
        }
        for a in aircraft
        if a['lat'] is not None and a['lon'] is not None
    ]

    return render_template(
        'dashboard.html',
        aircraft=aircraft[:limit],
        map_aircraft=map_aircraft,
        total_results=len(aircraft),
        stats=stats,
        error=data['error'],
        query=query,
        refresh_interval=safe_int(get_setting('refresh_interval', '15')) or 15,
        source_path=data.get('source_label', 'not configured'),
    )


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if is_installed():
        return redirect(url_for('index'))

    if request.method == 'POST':
        app_title = (request.form.get('app_title') or 'SkyJSON').strip()
        source_type = normalize_source_type(request.form.get('source_type'))
        aircraft_path = (request.form.get('aircraft_path') or '').strip()
        aircraft_url = (request.form.get('aircraft_url') or '').strip()
        rows_per_page = request.form.get('rows_per_page', '100').strip() or '100'
        refresh_interval = request.form.get('refresh_interval', '15').strip() or '15'
        enable_auth = request.form.get('enable_auth') == 'on'
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if source_type == 'file' and not aircraft_path:
            flash('Please enter a local path to aircraft.json.', 'danger')
            return render_template('setup.html')
        if source_type == 'url' and not aircraft_url:
            flash('Please enter a remote URL to aircraft.json.', 'danger')
            return render_template('setup.html')

        if enable_auth and (not username or not password):
            flash('When configuration protection is enabled, a username and password are required.', 'danger')
            return render_template('setup.html')

        set_setting('app_title', app_title)
        set_setting('source_type', source_type)
        set_setting('aircraft_path', aircraft_path)
        set_setting('aircraft_url', aircraft_url)
        set_setting('rows_per_page', rows_per_page)
        set_setting('refresh_interval', refresh_interval)
        set_setting('config_auth_enabled', '1' if enable_auth else '0')
        if enable_auth:
            set_setting('config_username', username)
            set_setting('config_password_hash', generate_password_hash(password))
        set_setting('installed', '1')
        flash('SkyJSON has been configured successfully.', 'success')
        return redirect(url_for('index'))

    return render_template('setup.html')


@app.route('/config/login', methods=['GET', 'POST'])
def config_login():
    if not config_auth_enabled():
        return redirect(url_for('config'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        stored_username = get_setting('config_username', '')
        stored_hash = get_setting('config_password_hash', '')
        if username == stored_username and stored_hash and check_password_hash(stored_hash, password):
            session['config_logged_in'] = True
            flash('Logged in successfully.', 'success')
            return redirect(request.args.get('next') or url_for('config'))
        flash('Invalid credentials.', 'danger')

    return render_template('login.html')


@app.route('/config/logout')
def config_logout():
    session.pop('config_logged_in', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/config', methods=['GET', 'POST'])
@config_login_required
def config():
    if request.method == 'POST':
        source_type = normalize_source_type(request.form.get('source_type'))
        aircraft_path = (request.form.get('aircraft_path') or '').strip()
        aircraft_url = (request.form.get('aircraft_url') or '').strip()

        if source_type == 'file' and not aircraft_path:
            flash('Please enter a local path to aircraft.json.', 'danger')
            return redirect(url_for('config'))
        if source_type == 'url' and not aircraft_url:
            flash('Please enter a remote URL to aircraft.json.', 'danger')
            return redirect(url_for('config'))

        set_setting('app_title', (request.form.get('app_title') or 'SkyJSON').strip())
        set_setting('source_type', source_type)
        set_setting('aircraft_path', aircraft_path)
        set_setting('aircraft_url', aircraft_url)
        set_setting('rows_per_page', request.form.get('rows_per_page', '100').strip() or '100')
        set_setting('refresh_interval', request.form.get('refresh_interval', '15').strip() or '15')

        enable_auth = request.form.get('enable_auth') == 'on'
        set_setting('config_auth_enabled', '1' if enable_auth else '0')

        if enable_auth:
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            if username:
                set_setting('config_username', username)
            if password:
                set_setting('config_password_hash', generate_password_hash(password))

        flash('Configuration saved.', 'success')
        return redirect(url_for('config'))

    update_status = get_update_status()
    source_settings = get_source_settings()
    settings = {
        'source_type': source_settings['source_type'],
        'aircraft_path': source_settings['aircraft_path'],
        'aircraft_url': source_settings['aircraft_url'],
        'rows_per_page': get_setting('rows_per_page', '100'),
        'refresh_interval': get_setting('refresh_interval', '15'),
        'config_username': get_setting('config_username', ''),
    }
    return render_template('config.html', update_status=update_status, settings=settings)


@app.route('/config/update', methods=['POST'])
@config_login_required
def update_app():
    status = get_update_status()
    if not status.get('supported'):
        flash(status.get('message', 'Updates are not supported for this installation.'), 'danger')
        return redirect(url_for('config'))

    pull = run_cmd(['git', 'pull', '--ff-only'])
    if pull['ok']:
        flash('SkyJSON was updated successfully. Restart the application service if needed.', 'success')
    else:
        flash(f"Update failed: {pull['stderr'] or pull['stdout']}", 'danger')
    return redirect(url_for('config'))


@app.route('/health')
def health():
    return {'status': 'ok', 'app': 'SkyJSON', 'version': APP_VERSION}


if __name__ == '__main__':
    host = os.environ.get('SKYJSON_HOST', '0.0.0.0')
    port = int(os.environ.get('SKYJSON_PORT', '8000'))
    debug = os.environ.get('SKYJSON_DEBUG', '0') == '1'
    app.run(host=host, port=port, debug=debug)
