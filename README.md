# Huawei B535 Signal Monitor

A desktop signal monitor for the **Huawei 4G Router B535-232a**. The app uses PyQt5 for the UI and Playwright to read RSRP and SINR values from the router WebUI in real time.

## Features

- Real-time RSRP and SINR monitoring.
- Modern PyQt desktop interface with metric cards, trend chart, and event log.
- Local configuration through `settings.ini`.
- Passwords are not stored in source code.
- Safe example config in `settings.example.ini`.

## Compatibility

Tested with:

- Huawei 4G Router B535-232a

This project is currently designed for Huawei B535-style WebUI pages that expose:

- login page at `http://192.168.8.1/html/index.html`
- password field selector `#login_password`
- signal page at `/html/content.html#deviceinformation`
- RSRP/SINR fields compatible with `#deviceinformation_rsrp`, `#di-rsrp`, `#deviceinformation.sinr`, or `#di-sinr`

Other Huawei, SoyeaLink, or carrier-customized routers may use different admin panels. They might require a new login flow, different selectors, or a future API-based backend.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Configuration

Copy the example file:

```powershell
Copy-Item settings.example.ini settings.ini
```

Then edit `settings.ini`:

```ini
[connection]
login_url = http://192.168.8.1/html/index.html
info_url = http://192.168.8.1/html/content.html#deviceinformation
password = your_router_password_here

[runtime]
refresh_seconds = 2
headless = true
```

For better privacy, avoid saving the password in `settings.ini` and use an environment variable instead:

```powershell
$env:RSRP_MODEM_PASSWORD = "your_router_password_here"
```

`settings.ini` is listed in `.gitignore` and should not be committed.

## Usage

```powershell
python main.py
```

Press **Start** to begin monitoring and **Stop** to end the session.

## Notes

The app automates the router WebUI with Playwright. If your router admin panel looks different, the app may open the page but fail to find the login or signal fields. In that case, support can be added by introducing a model-specific WebUI adapter or a Huawei HiLink API backend.
