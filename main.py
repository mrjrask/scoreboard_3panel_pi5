#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request
from PIL import Image, ImageDraw, ImageFont

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
        if action == "score_a_inc": self.score_a += 1
        elif action == "score_a_dec": self.score_a -= 1
        elif action == "score_b_inc": self.score_b += 1
        elif action == "score_b_dec": self.score_b -= 1
        elif action == "inning_inc": self.inning += 1
        elif action == "inning_dec": self.inning -= 1
        elif action == "half_toggle": self.inning_half = "bottom" if self.inning_half == "top" else "top"
        elif action == "balls_cycle": self.balls = (self.balls + 1) % 4
        elif action == "strikes_cycle": self.strikes = (self.strikes + 1) % 3
        elif action == "outs_cycle": self.outs = (self.outs + 1) % 3
        elif action == "reset":
            self.score_a = self.score_b = 0
            self.inning = 1
            self.inning_half = "top"
            self.balls = self.strikes = self.outs = 0
        self.clamp()


def load_state() -> ScoreboardState:
    if STATE_FILE.exists():
        s = ScoreboardState(**json.loads(STATE_FILE.read_text()))
        s.clamp()
        return s
    return ScoreboardState()


def save_state(state: ScoreboardState) -> None:
    STATE_FILE.write_text(json.dumps(asdict(state)))


class PiomatterDisplay:
    def __init__(self, width: int, height: int, bit_depth: int, chain_across: int, chain_down: int):
        self.width = width
        self.height = height
        self._driver = self._init_driver(width, height, bit_depth, chain_across, chain_down)

    def _init_driver(self, width: int, height: int, bit_depth: int, chain_across: int, chain_down: int):
        errors = []
        try:
            import piomatter
            return piomatter.PioMatter(width=width, height=height, bit_depth=bit_depth)
        except Exception as exc:
            errors.append(f"piomatter.PioMatter: {exc}")

        try:
            from adafruit_blinka_raspberry_pi5_piomatter import rgbmatrix
            return rgbmatrix.RGBMatrix(width=width, height=height, bit_depth=bit_depth, chain_across=chain_across, chain_down=chain_down)
        except Exception as exc:
            errors.append(f"adafruit...rgbmatrix.RGBMatrix: {exc}")

        raise RuntimeError(
            "Unable to initialize Blinka Pi5 Piomatter driver. "
            "Install Adafruit_Blinka_Raspberry_Pi5_Piomatter and verify API compatibility. "
            + " | ".join(errors)
        )

    def show(self, image: Image.Image, brightness: int) -> None:
        if hasattr(self._driver, "brightness"):
            self._driver.brightness = brightness / 100.0
        if hasattr(self._driver, "show"):
            self._driver.show(image)
        elif hasattr(self._driver, "image") and hasattr(self._driver, "refresh"):
            self._driver.image = image
            self._driver.refresh()
        else:
            raise RuntimeError("Piomatter driver missing supported frame output method")


class MatrixRenderer:
    def __init__(self, display: PiomatterDisplay, state: ScoreboardState):
        self.display = display
        self.state = state
        self.lock = threading.Lock()
        self.font = ImageFont.load_default()

    def draw(self) -> None:
        with self.lock:
            image = Image.new("RGB", (self.display.width, self.display.height), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            white, amber, red, green = (255, 255, 255), (255, 180, 0), (255, 50, 50), (60, 255, 60)
            panel_w = self.display.width // 3

            def block(x: int, title: str, team: str, score: int):
                draw.text((x + 2, 2), title, fill=white, font=self.font)
                draw.text((x + 2, 16), team, fill=white, font=self.font)
                draw.text((x + panel_w - 12, 16), str(score), fill=amber, font=self.font)

            block(0, "AWAY", self.state.team_a, self.state.score_a)
            block(panel_w, "HOME", self.state.team_b, self.state.score_b)
            x = panel_w * 2
            half = "TOP" if self.state.inning_half == "top" else "BOT"
            draw.text((x + 2, 2), f"{half} {self.state.inning}", fill=white, font=self.font)
            draw.text((x + 2, 16), f"B{self.state.balls} S{self.state.strikes}", fill=green, font=self.font)
            draw.text((x + 2, 28), f"OUT {self.state.outs}", fill=red, font=self.font)
            self.display.show(image, self.state.brightness)


HTML = """<!doctype html><html><body><h1>Scoreboard</h1>
<form method='post' action='/rename'>
<input name='team_a' value='{{s.team_a}}' maxlength='{{max_team_chars}}'>
<input name='team_b' value='{{s.team_b}}' maxlength='{{max_team_chars}}'>
<button>Save</button></form>
<p>AWAY {{s.score_a}} | HOME {{s.score_b}} | {{s.inning_half|upper}} {{s.inning}} | B{{s.balls}} S{{s.strikes}} O{{s.outs}}</p>
{% for label,a in [('Away +1','score_a_inc'),('Away -1','score_a_dec'),('Home +1','score_b_inc'),('Home -1','score_b_dec'),('Inning +1','inning_inc'),('Inning -1','inning_dec'),('Toggle Top/Bot','half_toggle'),('Cycle Balls','balls_cycle'),('Cycle Strikes','strikes_cycle'),('Cycle Outs','outs_cycle'),('Reset','reset')] %}
<form method='post' action='/action/{{a}}'><button>{{label}}</button></form>{% endfor %}</body></html>"""


def create_app(state: ScoreboardState, renderer: MatrixRenderer) -> Flask:
    app = Flask(__name__)
    @app.get("/")
    def index(): return render_template_string(HTML, s=state, max_team_chars=MAX_TEAM_CHARS)
    @app.post("/action/<action>")
    def action(action: str):
        state.update(action); save_state(state); renderer.draw(); return redirect("/")
    @app.post("/rename")
    def rename():
        state.team_a = request.form.get("team_a", state.team_a); state.team_b = request.form.get("team_b", state.team_b)
        state.clamp(); save_state(state); renderer.draw(); return redirect("/")
    @app.get('/state')
    def get_state(): return jsonify(asdict(state))
    return app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--panel-width", type=int, default=64)
    p.add_argument("--panel-height", type=int, default=32)
    p.add_argument("--chain-across", type=int, default=3)
    p.add_argument("--chain-down", type=int, default=1)
    p.add_argument("--bit-depth", type=int, default=6)
    p.add_argument("--brightness", type=int, default=70)
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    state = load_state(); state.brightness = args.brightness
    width = args.panel_width * args.chain_across
    height = args.panel_height * args.chain_down
    display = PiomatterDisplay(width, height, args.bit_depth, args.chain_across, args.chain_down)
    renderer = MatrixRenderer(display, state)
    renderer.draw()
    create_app(state, renderer).run(host=args.listen, port=args.port)


if __name__ == "__main__":
    main()
