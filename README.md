# SkyJSON

**A lightweight dashboard for real-time aircraft data.**

SkyJSON is a self-hosted Linux web application that reads aircraft data from an `aircraft.json` file and displays it in a clean, searchable dashboard.

## Highlights

- Web-based first-run installation wizard
- Configure a **local file** or a **remote JSON URL** from the browser
- Optional password protection for the configuration panel
- Dashboard with search, stats, map view, and automatic refresh
- Built-in **Update from GitHub** button in the configuration panel
- Built-in **Restart app** button in the configuration panel
- Optional **GitHub donation page** link shown in the app
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

## Restart behavior

SkyJSON now supports restarting from the configuration panel.

You can use either:

- a restart command, such as `systemctl restart skyjson`
- self-exit mode, which works when SkyJSON is managed by a supervisor that automatically restarts the app

## GitHub donation page

You can set a GitHub donation page URL in the installer or configuration panel. When set, a donation link appears in the app navigation and dashboard.

## License

MIT
