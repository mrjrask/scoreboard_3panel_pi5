#!/usr/bin/env python3
"""Baseball scoreboard for Raspberry Pi + Adafruit Triple LED Matrix Bonnet.

Target display: 3x 32x64 P5 HUB75 panels configured as one 96x64 matrix.
Uses rpi-rgb-led-matrix for rendering and Flask for local web control.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
from wsgiref.simple_server import make_server

STATE_FILE = Path("scoreboard_state.json")
MAX_TEAM_CHARS = 5


@dataclass
class ScoreboardState:
    team_a: str = "AWAY"
    team_b: str = "HOME"
    score_a: int = 0
    score_b: int = 0
    inning: int = 1
    inning_half: str = "top"
    balls: int = 0
    strikes: int = 0
    outs: int = 0
    brightness: int = 70

    def clamp(self) -> None:
        self.team_a = (self.team_a or "AWAY").strip().upper()[:MAX_TEAM_CHARS]
        self.team_b = (self.team_b or "HOME").strip().upper()[:MAX_TEAM_CHARS]
        self.score_a = max(0, self.score_a)
        self.score_b = max(0, self.score_b)
        self.inning = max(1, self.inning)
        self.balls = min(max(0, self.balls), 3)
        self.strikes = min(max(0, self.strikes), 2)
        self.outs = min(max(0, self.outs), 2)
        self.brightness = min(max(5, int(self.brightness)), 100)
        if self.inning_half not in {"top", "bottom"}:
            self.inning_half = "top"

    def update(self, action: str) -> None:
        if action == "score_a_inc":
            self.score_a += 1
        elif action == "score_a_dec":
            self.score_a -= 1
        elif action == "score_b_inc":
            self.score_b += 1
        elif action == "score_b_dec":
            self.score_b -= 1
        elif action == "inning_inc":
            self.inning += 1
        elif action == "inning_dec":
            self.inning -= 1
        elif action == "half_toggle":
            self.inning_half = "bottom" if self.inning_half == "top" else "top"
        elif action == "balls_cycle":
            self.balls = (self.balls + 1) % 4
        elif action == "strikes_cycle":
            self.strikes = (self.strikes + 1) % 3
        elif action == "outs_cycle":
            self.outs = (self.outs + 1) % 3
        elif action == "reset":
            self.score_a = self.score_b = 0
            self.inning = 1
            self.inning_half = "top"
            self.balls = self.strikes = self.outs = 0
        self.clamp()


def load_state() -> ScoreboardState:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        s = ScoreboardState(**data)
        s.clamp()
        return s
    return ScoreboardState()


def save_state(state: ScoreboardState) -> None:
    STATE_FILE.write_text(json.dumps(asdict(state)))


class MatrixRenderer:
    def __init__(self, matrix: RGBMatrix, state: ScoreboardState, font_name: str):
        self.matrix = matrix
        self.state = state
        self.lock = threading.Lock()
        self.font = graphics.Font()
        font_candidates = [
            Path("fonts") / font_name,
            Path("fonts/5x7.bdf"),
            Path("fonts/6x10.bdf"),
            Path("fonts/6x12.bdf"),
            Path("fonts/6x13.bdf"),
            Path("fonts/7x13.bdf"),
            Path("/usr/local/share/rpi-rgb-led-matrix/fonts/7x13.bdf"),
            Path("/usr/local/share/rpi-rgb-led-matrix/fonts/6x13.bdf"),
            Path("/usr/share/rpi-rgb-led-matrix/fonts/7x13.bdf"),
            Path("/usr/share/rpi-rgb-led-matrix/fonts/6x13.bdf"),
            # Debian-based fallback paths for BDF fonts.
            Path("/usr/share/fonts/X11/misc/7x13.bdf"),
            Path("/usr/share/fonts/X11/misc/6x13.bdf"),
            Path("/usr/share/fonts/misc/7x13.bdf"),
            Path("/usr/share/fonts/misc/6x13.bdf"),
        ]

        font_path = next((fp for fp in font_candidates if fp.exists()), None)

        # Last-resort scan for common LED-matrix font names.
        if font_path is None:
            for root in (Path("/usr/share"), Path("/usr/local/share")):
                if not root.exists():
                    continue
                for name in ("7x13.bdf", "6x13.bdf"):
                    match = next(root.rglob(name), None)
                    if match is not None:
                        font_path = match
                        break
                if font_path is not None:
                    break

        if font_path is None:
            raise FileNotFoundError("No compatible BDF font found. Check the project's fonts/ directory.")

        self.font.LoadFont(str(font_path))

    def draw(self) -> None:
        with self.lock:
            self.matrix.brightness = self.state.brightness
            canvas = self.matrix.CreateFrameCanvas()
            canvas.Clear()

            white = graphics.Color(255, 255, 255)
            amber = graphics.Color(255, 180, 0)
            red = graphics.Color(255, 50, 50)
            green = graphics.Color(60, 255, 60)

            panel_horizontal = self.matrix.width >= self.matrix.height
            panel_width = max(1, self.matrix.width // 3) if panel_horizontal else self.matrix.width
            panel_height = self.matrix.height if panel_horizontal else max(1, self.matrix.height // 3)

            def draw_panel_block(x: int, y: int, title: str, team: str, score: int) -> None:
                graphics.DrawText(canvas, self.font, x + 2, y + 10, white, title)
                graphics.DrawText(canvas, self.font, x + 2, y + 22, white, team)
                graphics.DrawText(canvas, self.font, x + panel_width - 14, y + 22, amber, str(score))

            if panel_horizontal:
                draw_panel_block(0, 0, "AWAY", self.state.team_a, self.state.score_a)
                draw_panel_block(panel_width, 0, "HOME", self.state.team_b, self.state.score_b)
                p3x, p3y = panel_width * 2, 0
            else:
                draw_panel_block(0, 0, "AWAY", self.state.team_a, self.state.score_a)
                draw_panel_block(0, panel_height, "HOME", self.state.team_b, self.state.score_b)
                p3x, p3y = 0, panel_height * 2

            half = "TOP" if self.state.inning_half == "top" else "BOT"
            graphics.DrawText(canvas, self.font, p3x + 2, p3y + 10, white, f"{half} {self.state.inning}")
            graphics.DrawText(canvas, self.font, p3x + 2, p3y + 22, green, f"B{self.state.balls} S{self.state.strikes}")
            graphics.DrawText(canvas, self.font, p3x + 2, p3y + 31, red, f"OUT {self.state.outs}")

            self.matrix.SwapOnVSync(canvas)


def build_matrix(args: argparse.Namespace) -> RGBMatrix:
    options = RGBMatrixOptions()
    options.rows = args.rows
    options.cols = args.cols
    options.chain_length = args.chain
    options.parallel = args.parallel
    options.gpio_slowdown = args.gpio_slowdown
    options.hardware_mapping = args.hardware_mapping
    options.multiplexing = args.multiplexing
    options.row_address_type = args.row_address_type
    options.scan_mode = 0
    options.pwm_bits = args.pwm_bits
    options.brightness = args.brightness
    if args.led_no_hardware_pulse:
        options.disable_hardware_pulsing = True
    if args.pixel_mapper:
        options.pixel_mapper_config = args.pixel_mapper
    return RGBMatrix(options=options)


HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Scoreboard Control</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: Arial, sans-serif; margin: 0; padding: 16px; background:#111; color:#f0f0f0; }
    h1 { margin-top: 0; }
    .card { background:#1e1e1e; border:1px solid #333; border-radius:10px; padding:12px; margin-bottom:12px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    input { background:#000; color:#fff; border:1px solid #555; border-radius:6px; padding:8px; min-width:100px; }
    button { background:#2f6fed; color:#fff; border:0; border-radius:8px; padding:10px 12px; cursor:pointer; }
    button:hover { background:#2458bc; }
    .actions { display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:8px; }
    .actions form { margin:0; }
    .actions button { width:100%; }
    .status { font-family: monospace; font-size: 1rem; }
  </style>
</head>
<body>
  <h1>Scoreboard Control</h1>

  <div class="card">
    <form method="post" action="/rename" class="row">
      <label>Away Team <input name="team_a" value="{{s.team_a}}" maxlength="{{max_team_chars}}"></label>
      <label>Home Team <input name="team_b" value="{{s.team_b}}" maxlength="{{max_team_chars}}"></label>
      <button type="submit">Save Team Names</button>
    </form>
  </div>

  <div class="card status">
    AWAY {{s.score_a}} | HOME {{s.score_b}}<br>
    {{s.inning_half|upper}} {{s.inning}} | B{{s.balls}} S{{s.strikes}} O{{s.outs}}
  </div>

  <div class="card actions">
    {% for label, a in [
      ('Away +1', 'score_a_inc'), ('Away -1', 'score_a_dec'),
      ('Home +1', 'score_b_inc'), ('Home -1', 'score_b_dec'),
      ('Inning +1', 'inning_inc'), ('Inning -1', 'inning_dec'),
      ('Toggle Top/Bot', 'half_toggle'),
      ('Cycle Balls', 'balls_cycle'), ('Cycle Strikes', 'strikes_cycle'), ('Cycle Outs', 'outs_cycle'),
      ('Reset Game', 'reset')
    ] %}
      <form method="post" action="/action/{{a}}"><button>{{label}}</button></form>
    {% endfor %}
  </div>
</body>
</html>
"""



def create_app(state: ScoreboardState, renderer: MatrixRenderer) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(HTML, s=state, max_team_chars=MAX_TEAM_CHARS)

    @app.post("/action/<action>")
    def action(action: str):
        state.update(action)
        save_state(state)
        renderer.draw()
        return redirect("/")

    @app.post("/rename")
    def rename():
        state.team_a = request.form.get("team_a", state.team_a)
        state.team_b = request.form.get("team_b", state.team_b)
        state.clamp()
        save_state(state)
        renderer.draw()
        return redirect("/")

    @app.get('/state')
    def get_state():
        return jsonify(asdict(state))

    return app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=32)
    p.add_argument("--cols", type=int, default=64)
    p.add_argument("--chain", type=int, default=1)
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--hardware-mapping", default="regular")
    p.add_argument("--multiplexing", type=int, default=0)
    p.add_argument("--row-address-type", type=int, default=0)
    p.add_argument("--gpio-slowdown", type=int, default=4)
    p.add_argument("--pwm-bits", type=int, default=11)
    p.add_argument("--brightness", type=int, default=70)
    p.add_argument("--font", default="5x7.bdf", help="Font file name from ./fonts (e.g. 5x7.bdf)")
    p.add_argument("--pixel-mapper", default="", help="Pixel mapper config (optional; e.g. Rotate:90)")
    p.add_argument("--led-no-hardware-pulse", action="store_true")
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    return p.parse_args()


def _has_werkzeug_metadata() -> bool:
    try:
        importlib.metadata.version("werkzeug")
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _run_with_wsgiref(app: Flask, listen: str, port: int) -> None:
    try:
        with make_server(listen, port, app) as server:
            server.serve_forever()
    except OSError as exc:
        if exc.errno == 98:
            raise SystemExit(
                f"Port {port} is already in use on {listen}. Stop the other process or pass --port with a free port."
            ) from exc
        raise


def main() -> None:
    args = parse_args()
    state = load_state()
    state.brightness = args.brightness
    matrix = build_matrix(args)
    renderer = MatrixRenderer(matrix, state, args.font)
    renderer.draw()
    app = create_app(state, renderer)

    if not _has_werkzeug_metadata():
        # Some Pi environments hit a metadata lookup failure for Werkzeug only
        # when Flask starts the dev server under sudo. Fall back to stdlib WSGI.
        print("WARN: Werkzeug package metadata lookup failed; falling back to wsgiref server.")
        _run_with_wsgiref(app, args.listen, args.port)
        return

    try:
        app.run(host=args.listen, port=args.port)
    except importlib.metadata.PackageNotFoundError as exc:
        if "werkzeug" not in str(exc).lower():
            raise
        print("WARN: Werkzeug package metadata lookup failed; falling back to wsgiref server.")
        _run_with_wsgiref(app, args.listen, args.port)


if __name__ == "__main__":
    main()
