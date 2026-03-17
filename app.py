
import json
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
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'config.db'
DEFAULT_SECRET = 'change-me-skyjson-secret'
APP_VERSION = '1.8.3'
REQUEST_TIMEOUT = 10
GITHUB_SPONSOR_URL = 'https://github.com/sponsors/PatrickS86'
VERSION_FILE = BASE_DIR / 'VERSION'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SKYJSON_SECRET_KEY', DEFAULT_SECRET)


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


def read_version_from_text(text: str) -> Optional[str]:
    match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def read_version_from_path(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        return read_version_from_text(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def get_running_version() -> str:
    return APP_VERSION


def get_local_file_version() -> str:
    return read_version_from_path(BASE_DIR / 'app.py') or APP_VERSION


def get_head_version() -> Optional[str]:
    if not (BASE_DIR / '.git').exists():
        return None
    show = run_cmd(['git', 'show', 'HEAD:app.py'])
    if show['ok'] and show['stdout']:
        return read_version_from_text(show['stdout'])
    return None


def get_upstream_ref() -> Optional[str]:
    if not (BASE_DIR / '.git').exists():
        return None
    upstream = run_cmd(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])
    if upstream['ok'] and upstream['stdout']:
        return upstream['stdout']
    return None


def get_remote_version() -> Optional[str]:
    upstream = get_upstream_ref()
    if not upstream:
        return None
    show = run_cmd(['git', 'show', f'{upstream}:VERSION'])
    if show['ok'] and show['stdout']:
        return show['stdout'].strip()
    show = run_cmd(['git', 'show', f'{upstream}:app.py'])
    if show['ok'] and show['stdout']:
        return read_version_from_text(show['stdout'])
    return None


def parse_version(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not version:
        return None
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def compare_versions(current: Optional[str], remote: Optional[str]) -> Optional[str]:
    current_parsed = parse_version(current)
    remote_parsed = parse_version(remote)
    if not current_parsed or not remote_parsed or remote_parsed <= current_parsed:
        return None
    if remote_parsed[0] > current_parsed[0]:
        return 'major'
    if remote_parsed[1] > current_parsed[1]:
        return 'minor'
    return 'patch'


def get_repo_status() -> Dict[str, Any]:
    running_version = get_running_version()
    local_file_version = get_local_file_version()
    head_version = get_head_version()

    status = {
        'supported': (BASE_DIR / '.git').exists(),
        'running_version': running_version,
        'local_file_version': local_file_version,
        'head_version': head_version or '—',
        'remote_version': '—',
        'update_kind': None,
        'branch': '—',
        'current_commit': '—',
        'remote_commit': '—',
        'ahead_count': '0',
        'behind_count': '0',
        'dirty': False,
        'message': 'Git repository not detected.',
    }

    if not status['supported']:
        return status

    fetch = run_cmd(['git', 'fetch', '--tags', 'origin'])
    if not fetch['ok']:
        status['message'] = f"Git fetch failed: {fetch['stderr'] or fetch['stdout']}"
        return status

    branch = run_cmd(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    head_commit = run_cmd(['git', 'rev-parse', 'HEAD'])
    upstream = get_upstream_ref()
    dirty = run_cmd(['git', 'status', '--porcelain'])

    if branch['ok'] and branch['stdout']:
        status['branch'] = branch['stdout']
    if head_commit['ok'] and head_commit['stdout']:
        status['current_commit'] = head_commit['stdout'][:7]
    status['dirty'] = bool(dirty['stdout'])

    if not upstream:
        status['message'] = 'No upstream branch is configured for this repository.'
        return status

    remote_commit = run_cmd(['git', 'rev-parse', upstream])
    ahead_count = run_cmd(['git', 'rev-list', '--count', f'{upstream}..HEAD'])
    behind_count = run_cmd(['git', 'rev-list', '--count', f'HEAD..{upstream}'])
    remote_version = get_remote_version()

    if remote_commit['ok'] and remote_commit['stdout']:
        status['remote_commit'] = remote_commit['stdout'][:7]
    if ahead_count['ok'] and ahead_count['stdout']:
        status['ahead_count'] = ahead_count['stdout']
    if behind_count['ok'] and behind_count['stdout']:
        status['behind_count'] = behind_count['stdout']
    if remote_version:
        status['remote_version'] = remote_version

    status['update_kind'] = compare_versions(local_file_version, remote_version)

    if status['behind_count'] != '0' and status['update_kind']:
        status['message'] = f"A {status['update_kind']} update is available on GitHub."
    elif status['behind_count'] != '0':
        status['message'] = 'A Git update is available on GitHub.'
    elif status['running_version'] != status['local_file_version']:
        status['message'] = 'The running app version does not match the app.py file on disk. Restart is required.'
    elif status['head_version'] != '—' and status['local_file_version'] != status['head_version']:
        status['message'] = 'The app.py file on disk does not match Git HEAD.'
    elif status['dirty']:
        status['message'] = 'Repository has local uncommitted changes.'
    else:
        status['message'] = 'SkyJSON is up to date.'

    return status


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


def get_github_donation_url() -> str:
    return GITHUB_SPONSOR_URL


@app.context_processor
def inject_globals():
    return {
        'app_title': 'SkyJSON',
        'app_version': get_local_file_version(),
        'running_version': get_running_version(),
        'config_auth_enabled': config_auth_enabled(),
        'is_logged_in': is_logged_in(),
        'github_donation_url': get_github_donation_url(),
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
    return 'url' if source_type == 'url' else 'file'


def get_source_settings() -> Dict[str, str]:
    source_type = normalize_source_type(get_setting('source_type', 'file'))
    aircraft_path = get_setting('aircraft_path', '') or ''
    aircraft_url = get_setting('aircraft_url', '') or ''
    return {
        'source_type': source_type,
        'aircraft_path': aircraft_path,
        'aircraft_url': aircraft_url,
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


REGISTRATION_PREFIX_FLAGS = [
    ('PH-', 'Netherlands', 'NL', '🇳🇱'), ('OO-', 'Belgium', 'BE', '🇧🇪'), ('D-', 'Germany', 'DE', '🇩🇪'),
    ('F-', 'France', 'FR', '🇫🇷'), ('G-', 'United Kingdom', 'GB', '🇬🇧'), ('EI-', 'Ireland', 'IE', '🇮🇪'),
    ('LX-', 'Luxembourg', 'LU', '🇱🇺'), ('HB-', 'Switzerland', 'CH', '🇨🇭'), ('OE-', 'Austria', 'AT', '🇦🇹'),
    ('I-', 'Italy', 'IT', '🇮🇹'), ('EC-', 'Spain', 'ES', '🇪🇸'), ('CS-', 'Portugal', 'PT', '🇵🇹'),
    ('SE-', 'Sweden', 'SE', '🇸🇪'), ('LN-', 'Norway', 'NO', '🇳🇴'), ('OY-', 'Denmark', 'DK', '🇩🇰'),
    ('OH-', 'Finland', 'FI', '🇫🇮'), ('TF-', 'Iceland', 'IS', '🇮🇸'), ('SP-', 'Poland', 'PL', '🇵🇱'),
    ('OK-', 'Czech Republic', 'CZ', '🇨🇿'), ('OM-', 'Slovakia', 'SK', '🇸🇰'), ('YR-', 'Romania', 'RO', '🇷🇴'),
    ('HA-', 'Hungary', 'HU', '🇭🇺'), ('S5-', 'Slovenia', 'SI', '🇸🇮'), ('9A-', 'Croatia', 'HR', '🇭🇷'),
    ('YU-', 'Serbia', 'RS', '🇷🇸'), ('LZ-', 'Bulgaria', 'BG', '🇧🇬'), ('SX-', 'Greece', 'GR', '🇬🇷'),
    ('TC-', 'Turkey', 'TR', '🇹🇷'), ('N', 'United States', 'US', '🇺🇸'), ('C-', 'Canada', 'CA', '🇨🇦'),
]

HEX_PREFIX_FLAGS = [
    ('48', 'Netherlands', 'NL', '🇳🇱'), ('44', 'United Kingdom', 'GB', '🇬🇧'),
]


def get_country_info(hex_code: str, registration: str) -> Dict[str, str]:
    reg = (registration or '').strip().upper()
    hex_upper = (hex_code or '').strip().upper()
    for prefix, name, code, flag in REGISTRATION_PREFIX_FLAGS:
        if reg.startswith(prefix):
            return {'country': name, 'country_code': code, 'flag': flag}
    for prefix, name, code, flag in HEX_PREFIX_FLAGS:
        if hex_upper.startswith(prefix):
            return {'country': name, 'country_code': code, 'flag': flag}
    return {'country': 'Unknown', 'country_code': '', 'flag': '🏳️'}


def detect_signal_source(item: Dict[str, Any]) -> str:
    source_raw = str(item.get('type') or item.get('dbFlags') or '').strip().lower()
    if 'mlat' in source_raw:
        return 'MLAT'
    if 'mode_s' in source_raw or 'mode-s' in source_raw or source_raw == 'modes':
        return 'Mode-S'
    if 'adsb' in source_raw or 'ads-b' in source_raw:
        return 'ADS-B'
    if item.get('lat') is not None and item.get('lon') is not None:
        return 'ADS-B'
    return 'Unknown'


def load_aircraft() -> Dict[str, Any]:
    source_settings = get_source_settings()
    source_type = source_settings['source_type']
    payload = read_payload_from_url(source_settings['aircraft_url']) if source_type == 'url' else read_payload_from_file(source_settings['aircraft_path'])

    if payload.get('error'):
        return payload

    items = payload.get('aircraft', [])
    aircraft: List[Dict[str, Any]] = []
    for item in items:
        country_info = get_country_info(item.get('hex', '-'), item.get('r', ''))
        aircraft.append(
            {
                'hex': item.get('hex', '-'),
                'flight': (item.get('flight') or '').strip(),
                'registration': item.get('r', ''),
                'type': item.get('t', ''),
                'signal_source': detect_signal_source(item),
                'alt_baro': safe_int(item.get('alt_baro')),
                'gs': safe_float(item.get('gs')),
                'track': safe_float(item.get('track')),
                'lat': safe_float(item.get('lat')),
                'lon': safe_float(item.get('lon')),
                'messages': safe_int(item.get('messages')),
                'country': country_info['country'],
                'flag': country_info['flag'],
            }
        )

    aircraft.sort(key=lambda x: ((x['flight'] or x['registration'] or x['hex']).lower(), x['hex']))
    return {'error': None, 'aircraft': aircraft, 'now': payload.get('now')}


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


@app.route('/')
@require_installation
def index():
    data = load_aircraft()
    aircraft = data['aircraft']
    stats = summarize_aircraft(aircraft)
    query = (request.args.get('q') or '').strip().lower()

    if query:
        aircraft = [
            a for a in aircraft
            if query in (a['hex'] or '').lower()
            or query in (a['flight'] or '').lower()
            or query in (a['registration'] or '').lower()
            or query in (a['type'] or '').lower()
            or query in (a['country'] or '').lower()
            or query in (a['signal_source'] or '').lower()
        ]

    map_aircraft = [
        {
            'hex': a['hex'],
            'flight': a['flight'],
            'registration': a['registration'],
            'type': a['type'],
            'signal_source': a['signal_source'],
            'alt_baro': a['alt_baro'],
            'gs': a['gs'],
            'track': a['track'],
            'lat': a['lat'],
            'lon': a['lon'],
            'country': a['country'],
            'flag': a['flag'],
        }
        for a in aircraft
        if a['lat'] is not None and a['lon'] is not None
    ]

    return render_template(
        'dashboard.html',
        aircraft=aircraft,
        map_aircraft=map_aircraft,
        total_results=len(aircraft),
        stats=stats,
        error=data['error'],
        query=query,
        refresh_interval=1,
    )


@app.route('/api/aircraft')
@require_installation
def api_aircraft():
    data = load_aircraft()
    aircraft = data['aircraft']
    query = (request.args.get('q') or '').strip().lower()

    if query:
        aircraft = [
            a for a in aircraft
            if query in (a['hex'] or '').lower()
            or query in (a['flight'] or '').lower()
            or query in (a['registration'] or '').lower()
            or query in (a['type'] or '').lower()
            or query in (a['country'] or '').lower()
            or query in (a['signal_source'] or '').lower()
        ]

    return {
        'error': data.get('error'),
        'stats': summarize_aircraft(aircraft),
        'total_results': len(aircraft),
        'aircraft': aircraft,
        'map_aircraft': [
            {
                'hex': a['hex'],
                'flight': a['flight'],
                'registration': a['registration'],
                'type': a['type'],
                'signal_source': a['signal_source'],
                'alt_baro': a['alt_baro'],
                'gs': a['gs'],
                'track': a['track'],
                'lat': a['lat'],
                'lon': a['lon'],
                'country': a['country'],
                'flag': a['flag'],
            }
            for a in aircraft
            if a['lat'] is not None and a['lon'] is not None
        ]
    }


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if is_installed():
        return redirect(url_for('index'))

    if request.method == 'POST':
        source_type = normalize_source_type(request.form.get('source_type'))
        aircraft_path = (request.form.get('aircraft_path') or '').strip()
        aircraft_url = (request.form.get('aircraft_url') or '').strip()
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

        set_setting('source_type', source_type)
        set_setting('aircraft_path', aircraft_path)
        set_setting('aircraft_url', aircraft_url)
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
        password = (request.form.get('password') or '')
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

        set_setting('source_type', source_type)
        set_setting('aircraft_path', aircraft_path)
        set_setting('aircraft_url', aircraft_url)

        enable_auth = request.form.get('enable_auth') == 'on'
        set_setting('config_auth_enabled', '1' if enable_auth else '0')
        if enable_auth:
            username = (request.form.get('username') or '').strip()
            password = (request.form.get('password') or '').strip()
            if username:
                set_setting('config_username', username)
            if password:
                set_setting('config_password_hash', generate_password_hash(password))

        flash('Configuration saved.', 'success')
        return redirect(url_for('config'))

    update_status = get_repo_status()
    source_settings = get_source_settings()
    settings = {
        'source_type': source_settings['source_type'],
        'aircraft_path': source_settings['aircraft_path'],
        'aircraft_url': source_settings['aircraft_url'],
        'config_username': get_setting('config_username', ''),
    }
    return render_template('config.html', update_status=update_status, settings=settings)


@app.route('/config/update', methods=['POST'])
@config_login_required
def update_app():
    status = get_repo_status()
    if not status.get('supported'):
        flash(status.get('message', 'Updates are not supported for this installation.'), 'danger')
        return redirect(url_for('config'))
    pull = run_cmd(['git', 'pull', '--ff-only'])
    if pull['ok']:
        flash('SkyJSON was updated successfully. Restart the application to load the new version.', 'success')
    else:
        flash(f"Update failed: {pull['stderr'] or pull['stdout']}", 'danger')
    return redirect(url_for('config'))


@app.route('/config/restart', methods=['POST'])
@config_login_required
def restart_app():
    def delayed_restart_process() -> None:
        python_executable = sys.executable
        argv = [python_executable] + sys.argv
        quoted_cmd = ' '.join(shlex.quote(part) for part in argv)
        restart_script = f"sleep 2; cd {shlex.quote(str(BASE_DIR))} && exec {quoted_cmd}"
        subprocess.Popen(
            ['/bin/sh', '-c', restart_script],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=delayed_restart_process, daemon=True).start()
    return render_template('restart.html')


@app.route('/health')
def health():
    return {
        'status': 'ok',
        'app': 'SkyJSON',
        'running_version': get_running_version(),
        'file_version': get_local_file_version(),
        'head_version': get_head_version(),
        'remote_version': get_remote_version(),
    }


if __name__ == '__main__':
    host = os.environ.get('SKYJSON_HOST', '0.0.0.0')
    port = int(os.environ.get('SKYJSON_PORT', '8000'))
    debug = os.environ.get('SKYJSON_DEBUG', '0') == '1'
    app.run(host=host, port=port, debug=debug, use_reloader=False)
