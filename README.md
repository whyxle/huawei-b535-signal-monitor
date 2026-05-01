# Huawei B535 Signal Monitor

<p align="center">
  <img src="https://github.com/user-attachments/assets/cbf63acc-f139-4420-a88f-de48365dbde9" width="600"/>
</p>


A desktop signal monitor for the **Huawei 4G Router B535-232a**. The app uses PyQt5 for the UI and Playwright to read RSRP and SINR values from the router WebUI in real time.

It is especially useful when adjusting external router antennas: start monitoring, slowly change the antenna direction or placement, and watch how RSRP and SINR react in real time.

## Features

- Real-time RSRP and SINR monitoring.
- Modern PyQt desktop interface with metric cards, trend chart, and event log.
- Local configuration through `settings.ini`.
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

Download the latest Windows build from the project's **GitHub Releases** page:

1. Open **Releases** in the GitHub repository.
2. Download `rsrp-signal-monitor.zip` from the latest release.
3. Extract the archive to any folder.
4. Run `RSRP-Signal-Monitor.exe`.

The release archive includes the app and the Playwright browser files it needs, so a normal installation does not require Python, `pip`, or a separate Chromium install.

## Configuration

Create a local configuration file next to `RSRP_checker.exe`:

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

For better privacy, you can leave the password out of `settings.ini` and set it as an environment variable before launching the app:

```powershell
$env:RSRP_MODEM_PASSWORD = "your_router_password_here"
```

## Usage

Run `RSRP_checker.exe`, then press **Start** to begin monitoring and **Stop** to end the session.

While aiming an external antenna, keep the app open and adjust the antenna gradually. Better signal usually means a stronger RSRP value and a higher SINR value; wait a few refresh cycles after each movement before comparing readings.

## Development

To run the app from source:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python main.py
```

To build the Windows release folder:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller .\RSRP_checker.spec
Copy-Item settings.example.ini .\dist\RSRP_checker\settings.example.ini -Force
Compress-Archive -Path .\dist\RSRP_checker\* -DestinationPath .\dist\RSRP_checker-windows.zip -Force
```

The build output is created under `dist/`. Upload `dist/RSRP_checker-windows.zip` to GitHub Releases instead of committing it to the repository.

## Source Configuration

When running from source, `settings.ini` is read from the repository folder:

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

## Notes

The app automates the router WebUI with Playwright. If your router admin panel looks different, the app may open the page but fail to find the login or signal fields. In that case, support can be added by introducing a model-specific WebUI adapter or a Huawei HiLink API backend.
