# Raspberry Pi Baseball Scoreboard (96x64 HUB75 via Triple Bonnet)

This project targets a **Raspberry Pi** driving HUB75 panels through the **Adafruit Triple LED Matrix Bonnet**.

## Hardware target

- Raspberry Pi (40-pin GPIO)
- Adafruit Triple LED Matrix Bonnet
- 3x P5 outdoor HUB75 panels (SMD1921, 1/8 scan) arranged as a single **96x64** display
- 5V power supply sized for total panel current draw

## Display configuration used by default

Defaults are aligned to Triple Bonnet guidance (parallel outputs):

- `--rows 32`
- `--cols 64`
- `--chain 1`
- `--parallel 3`
- `--hardware-mapping regular`
- `--pixel-mapper` unset by default (no rotation)

If your wiring/orientation differs, override startup flags (for example `--pixel-mapper Rotate:90`).

> For single-bonnet/HAT wiring patterns, `adafruit-hat` or `adafruit-hat-pwm` may be appropriate. For this Triple Bonnet project, defaults assume `parallel=3` output mode.

## Install on Raspberry Pi

### 1) Run installer

```bash
./install.sh
```

The installer now handles:

- apt dependencies (`python3`, `python3-venv`, `python3-dev`, compiler toolchain, etc.)
- venv creation at `.venv`
- Python package install from `requirements.txt`
- auto-build/install of `rgbmatrix` bindings if missing


## Run

Because HUB75 GPIO access commonly requires elevated permissions, use:

```bash
sudo -E env PATH="$PATH" python3 main.py
```

Web control UI:

- `http://<pi-ip>:8080/`

State endpoint:

- `http://<pi-ip>:8080/state`

## Useful tuning for 1/8-scan outdoor panels

If startup reports `snd_bcm2835` audio-module incompatibility, either disable the Pi onboard audio module or run with:

```bash
sudo -E env PATH="$PATH" python3 main.py --led-no-hardware-pulse
```

(Using `--led-no-hardware-pulse` can increase flicker but avoids the hardware pulse conflict.)

If you see ghosting/flicker/color artifacts, test combinations of:

- `--row-address-type 0..4`
- `--multiplexing 0..17`
- `--gpio-slowdown 2..5`
- `--pwm-bits 7..11`

Example:

```bash
sudo -E env PATH="$PATH" python3 main.py --row-address-type 3 --multiplexing 0 --gpio-slowdown 4 --pwm-bits 9
```

## Fonts

`rgbmatrix.graphics.Font` requires **BDF** fonts (not TTF). The app looks for:

- `fonts/7x13.bdf`
- `fonts/6x13.bdf`
- `/usr/local/share/rpi-rgb-led-matrix/fonts/7x13.bdf`
- `/usr/local/share/rpi-rgb-led-matrix/fonts/6x13.bdf`
- `/usr/share/rpi-rgb-led-matrix/fonts/7x13.bdf`
- `/usr/share/rpi-rgb-led-matrix/fonts/6x13.bdf`

If none are found, startup fails with an explicit error.

## Optional: systemd service

Example unit (`/etc/systemd/system/scoreboard.service`):

```ini
[Unit]
Description=Raspberry Pi LED Matrix Scoreboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/pi/scoreboard_3panel
ExecStart=/usr/bin/sudo -E env PATH=/home/pi/.venv/bin:/usr/bin:/bin /home/pi/.venv/bin/python3 /home/pi/scoreboard_3panel/main.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scoreboard.service
```

## Files

- `main.py` – scoreboard renderer + Flask web controls for Raspberry Pi
- `requirements.txt` – Python dependencies for web/UI runtime
- `install.sh` – helper installer for Raspberry Pi
- `uninstall.sh` – removes Python dependencies installed by helper
