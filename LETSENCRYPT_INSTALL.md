SkyJSON automatic Linux install with Let's Encrypt

Run:
  sudo bash install.sh

What the installer does:
- installs Python, nginx and certbot
- creates a virtual environment
- installs SkyJSON dependencies
- creates a systemd service for SkyJSON
- enables the service at boot
- configures nginx as a reverse proxy
- optionally requests a Let's Encrypt certificate automatically
- enables HTTPS redirect if the certificate request succeeds

Requirements for automatic Let's Encrypt:
- a public domain name that already points to this server
- inbound port 80 reachable from the internet for HTTP-01 validation
- inbound port 443 reachable for normal HTTPS access

Notes:
- the app itself runs on 127.0.0.1 on an internal port
- nginx handles public HTTP/HTTPS traffic
- if automatic Let's Encrypt is skipped, HTTP still works through nginx
