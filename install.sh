#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"

echo "Updating apt package index..."
sudo apt update

echo "Installing system dependencies..."
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  python3-pil \
  libjpeg-dev \
  zlib1g-dev \
  build-essential \
  make \
  g++ \
  git

if apt-cache show python3-rpi-rgb-led-matrix >/dev/null 2>&1; then
  echo "Installing python3-rpi-rgb-led-matrix from apt..."
  sudo apt install -y python3-rpi-rgb-led-matrix
fi

echo "Creating virtual environment at: $VENV_DIR"
python3 -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Installing Python dependencies for web UI..."
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r "$REPO_DIR/requirements.txt"

if ! python - <<'PY'
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("rgbmatrix") else 1)
PY
then
  echo "rgbmatrix not found. Building and installing rpi-rgb-led-matrix Python bindings..."
  BUILD_DIR="$(mktemp -d)"
  trap 'rm -rf "$BUILD_DIR"' EXIT
  git clone --depth=1 https://github.com/hzeller/rpi-rgb-led-matrix.git "$BUILD_DIR/rpi-rgb-led-matrix"
  PYTHON_BIN="$(command -v python)"

  BUILD_TARGET=""
  INSTALL_TARGET=""
  BUILD_MAKE_DIR=""
  PIP_INSTALL_DIR=""

  has_make_target() {
    local make_dir="$1"
    local target="$2"
    make -C "$make_dir" -qp 2>/dev/null | awk -F':' -v t="$target" '
      $1 == t && $0 !~ /=/ { found=1 }
      END { exit(found ? 0 : 1) }
    '
  }

  for MAKE_DIR in \
    "$BUILD_DIR/rpi-rgb-led-matrix" \
    "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python"
  do
    [[ -f "$MAKE_DIR/Makefile" ]] || continue
    for TARGET_PAIR in \
      "build-python install-python" \
      "build-python3 install-python3" \
      "build-python" \
      "build-python3"
    do
      BUILD_CANDIDATE="${TARGET_PAIR%% *}"
      INSTALL_CANDIDATE="${TARGET_PAIR##* }"
      if [[ "$BUILD_CANDIDATE" == "$INSTALL_CANDIDATE" ]]; then
        if has_make_target "$MAKE_DIR" "$BUILD_CANDIDATE"; then
          BUILD_TARGET="$BUILD_CANDIDATE"
          BUILD_MAKE_DIR="$MAKE_DIR"
          PIP_INSTALL_DIR="$BUILD_DIR/rpi-rgb-led-matrix/bindings/python"
          break 2
        fi
      elif has_make_target "$MAKE_DIR" "$BUILD_CANDIDATE" && \
           has_make_target "$MAKE_DIR" "$INSTALL_CANDIDATE"; then
        BUILD_TARGET="$BUILD_CANDIDATE"
        INSTALL_TARGET="$INSTALL_CANDIDATE"
        BUILD_MAKE_DIR="$MAKE_DIR"
        break 2
      fi
    done
  done

  if [[ -n "$BUILD_TARGET" && -n "$INSTALL_TARGET" ]]; then
    make -C "$BUILD_MAKE_DIR" "$BUILD_TARGET" PYTHON="$PYTHON_BIN"
    sudo make -C "$BUILD_MAKE_DIR" "$INSTALL_TARGET" PYTHON="$PYTHON_BIN"
  elif [[ -n "$BUILD_TARGET" && -n "$PIP_INSTALL_DIR" ]]; then
    make -C "$BUILD_MAKE_DIR" "$BUILD_TARGET" PYTHON="$PYTHON_BIN"
    if compgen -G "$PIP_INSTALL_DIR/dist/*.whl" >/dev/null; then
      python -m pip install "$PIP_INSTALL_DIR"/dist/*.whl
    elif [[ -f "$PIP_INSTALL_DIR/setup.py" || -f "$PIP_INSTALL_DIR/pyproject.toml" ]]; then
      python -m pip install "$PIP_INSTALL_DIR"
    else
      echo "Built Python bindings, but could not find a wheel or installable project metadata."
      echo "Checked in: $PIP_INSTALL_DIR and $PIP_INSTALL_DIR/dist"
      exit 1
    fi
  elif [[ -f "$BUILD_DIR/rpi-rgb-led-matrix/pyproject.toml" ]]; then
    echo "No legacy python make target found; installing from repository root pyproject.toml..."
    python -m pip install "$BUILD_DIR/rpi-rgb-led-matrix"
  elif [[ -d "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python" ]]; then
    if compgen -G "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python/dist/*.whl" >/dev/null; then
      python -m pip install "$BUILD_DIR"/rpi-rgb-led-matrix/bindings/python/dist/*.whl
    elif [[ -f "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python/setup.py" || -f "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python/pyproject.toml" ]]; then
      python -m pip install "$BUILD_DIR/rpi-rgb-led-matrix/bindings/python"
    else
      echo "Found bindings/python, but it is not pip-installable."
      echo "Neither wheel(s) in dist/ nor setup.py/pyproject.toml was found."
      echo "Also checked for repository root pyproject.toml install path."
      exit 1
    fi
  else
    echo "Unable to find a compatible Python build/install method in rpi-rgb-led-matrix."
    echo "Checked for: build-python/install-python, build-python3/install-python3, build-python, build-python3"
    echo "Fallback checked: pip install from bindings/python and repository root pyproject.toml"
    echo "Searched in: repo root and bindings/python"
    exit 1
  fi
fi

cat <<MSG

Install complete.

Activate environment:
  source "$VENV_DIR/bin/activate"

Run scoreboard:
  sudo -E env PATH="$VENV_DIR/bin:\$PATH" python "$REPO_DIR/main.py"
MSG
