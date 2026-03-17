# Automatic Linux service installation

Run the installer as root or with sudo:

```bash
sudo bash install.sh
```

The installer will:
- ask which Linux user should run SkyJSON
- create or update `.venv`
- install Python dependencies
- create `/etc/default/skyjson`
- create `/etc/systemd/system/skyjson.service`
- enable the service at boot
- start or restart the service automatically
