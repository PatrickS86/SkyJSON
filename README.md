# SkyJSON

**A lightweight dashboard for real-time aircraft data.**

SkyJSON is a self-hosted Linux web application that reads aircraft data from an `aircraft.json` file and displays it in a clean, searchable dashboard.

## Highlights

- Web-based first-run installation wizard
- Configure a **local file** or a **remote JSON URL** from the browser
- Optional password protection for the configuration panel
- Dashboard with search, stats and automatic refresh
- Built-in **Update from GitHub** button in the configuration panel
- Designed to run on Linux with a simple Python setup

## Supported sources

SkyJSON supports aircraft data from:

- a local `aircraft.json` file
- a remote HTTP or HTTPS JSON endpoint

Examples:

```bash
/run/dump1090-fa/aircraft.json
/var/run/dump1090-fa/aircraft.json
http://192.168.1.50/aircraft.json
http://readsb.local/aircraft.json
```

## Quick start

Clone the repository:

```bash
git clone https://github.com/yourusername/skyjson.git
cd skyjson
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run SkyJSON:

```bash
python3 app.py
```

Open:

```text
http://localhost:8000
```

On first launch, SkyJSON opens the **web installer**. There you can:

1. Set the application title
2. Choose **Local file** or **Remote URL** as the aircraft data source
3. Enter the local path or remote URL for `aircraft.json`
4. Choose the refresh interval and dashboard size
5. Decide whether the configuration panel should be protected with a username and password

## Updating from GitHub

The configuration page includes an **Update from GitHub** button.

How it works:

- SkyJSON checks whether the app was installed from a Git repository
- It runs `git fetch origin` to compare your local version with the remote version
- When an update is available, you can install it from the browser
- The update action uses `git pull --ff-only`

Requirements for web updates:

- `git` must be installed on the server
- the application directory must be a cloned Git repository
- the user running SkyJSON must have write access to the repository folder
- if you use a service manager, restart the app after updating

## Configuration security

You can keep the dashboard public and only protect `/config`.

If configuration protection is enabled:

- the dashboard remains accessible
- the configuration page requires a username and password
- passwords are stored as hashed values in the local SQLite database

## Project structure

```text
skyjson/
├── app.py
├── config.db
├── requirements.txt
├── README.md
├── LICENSE
├── static/
│   ├── style.css
│   └── logo.png
└── templates/
    ├── base.html
    ├── config.html
    ├── dashboard.html
    ├── login.html
    └── setup.html
```

## Optional systemd service

Example:

```ini
[Unit]
Description=SkyJSON
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/skyjson
Environment="SKYJSON_PORT=8000"
ExecStart=/opt/skyjson/.venv/bin/python /opt/skyjson/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## License

MIT
