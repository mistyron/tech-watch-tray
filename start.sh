#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT/compose.yaml"
APP_ID="io.github.mistyron.TechWatchTray"
VENV_DIR="${TECH_WATCH_VENV:-$ROOT/.venv}"
PYTHON_BIN="${TECH_WATCH_PYTHON:-python3}"
MODE="${TECH_WATCH_OLLAMA_MODE:-auto}"
APP_MODE="${TECH_WATCH_APP_MODE:-host}"
INSTALL=1
PULL=1
DOWN=0
SMOKE=0
APP_ARGS=()

usage() {
    cat <<EOF
Usage: $0 [options] [--] [tech-watch args...]

Starts the native tray app and, when needed, a containerized Ollama service.

Options:
  --container-app     Run the tray app itself in Docker (with all Python/AI deps).
  --host-app          Run the tray app on the host (default).
  --container-ollama  Always use Ollama from Docker Compose.
  --host-ollama       Use the host's Ollama CLI/server.
  --no-ollama         Do not start or check Ollama.
  --no-install        Do not create/update the local .venv.
  --no-pull           Do not pull the configured Ollama model.
  --smoke-test        Set up Ollama/app containers, run smoke tests, and exit.
  --down              Stop the Docker Compose services and exit.
  -h, --help          Show this help.

Environment:
  TECH_WATCH_APP_MODE     host or container (default: host)
  TECH_WATCH_OLLAMA_MODE  auto, container, host, or off (default: auto)
  TECH_WATCH_VENV         Virtualenv directory (default: .venv)
  TECH_WATCH_PYTHON       Python used to create the virtualenv (default: python3)
  TECH_WATCH_INSTALL_AGY  Install agy into the app image (default: 1)
  TECH_WATCH_QT_PLATFORM  Override Qt platform inside the app container
  OLLAMA_MODEL            Model to ensure is present (default: config ai.ollama_model)

Examples:
  $0
  $0 --container-app
  $0 --container-app --debug
  $0 --container-app --no-ollama
  $0 --container-ollama --check-now
  $0 --host-ollama
  $0 --container-app --smoke-test
  $0 --down
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --container-app)
            APP_MODE=container
            shift
            ;;
        --host-app)
            APP_MODE=host
            shift
            ;;
        --container-ollama)
            MODE=container
            shift
            ;;
        --host-ollama)
            MODE=host
            shift
            ;;
        --no-ollama)
            MODE=off
            shift
            ;;
        --no-install)
            INSTALL=0
            shift
            ;;
        --no-pull)
            PULL=0
            shift
            ;;
        --smoke-test)
            SMOKE=1
            shift
            ;;
        --down)
            DOWN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            APP_ARGS+=("$@")
            break
            ;;
        *)
            APP_ARGS+=("$1")
            shift
            ;;
    esac
done

case "$MODE" in
    auto|container|host|off) ;;
    *)
        echo "Invalid TECH_WATCH_OLLAMA_MODE: $MODE (expected auto, container, host, or off)" >&2
        exit 2
        ;;
esac

case "$APP_MODE" in
    host|container) ;;
    *)
        echo "Invalid TECH_WATCH_APP_MODE: $APP_MODE (expected host or container)" >&2
        exit 2
        ;;
esac

setup_container_app_env() {
    TECH_WATCH_UID="$(id -u)"
    TECH_WATCH_GID="$(id -g)"
    TECH_WATCH_CONFIG_DIR="${TECH_WATCH_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/tech-watch}"
    TECH_WATCH_DATA_DIR="${TECH_WATCH_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/tech-watch}"
    if [[ -z "${TECH_WATCH_KDE_GLOBALS:-}" ]]; then
        for candidate in "${XDG_CONFIG_HOME:-$HOME/.config}/kdeglobals" "$HOME/.config/kdeglobals"; do
            if [[ -f "$candidate" ]]; then
                TECH_WATCH_KDE_GLOBALS="$candidate"
                break
            fi
        done
    fi
    TECH_WATCH_KDE_GLOBALS="${TECH_WATCH_KDE_GLOBALS:-/dev/null}"
    TECH_WATCH_RUNTIME_DIR="${TECH_WATCH_RUNTIME_DIR:-${XDG_RUNTIME_DIR:-/run/user/$TECH_WATCH_UID}}"
    if [[ ! -d "$TECH_WATCH_RUNTIME_DIR" || ! -w "$TECH_WATCH_RUNTIME_DIR" ]]; then
        TECH_WATCH_RUNTIME_DIR="${TMPDIR:-/tmp}/tech-watch-runtime-$TECH_WATCH_UID"
        if [[ ! -d "$TECH_WATCH_RUNTIME_DIR" || ! -w "$TECH_WATCH_RUNTIME_DIR" ]]; then
            TECH_WATCH_RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tech-watch-runtime-$TECH_WATCH_UID.XXXXXX")"
        fi
        chmod 700 "$TECH_WATCH_RUNTIME_DIR" 2>/dev/null || true
    fi
    if [[ -z "${TECH_WATCH_XAUTHORITY:-}" ]]; then
        if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
            TECH_WATCH_XAUTHORITY="$XAUTHORITY"
        elif [[ -f "$HOME/.Xauthority" ]]; then
            TECH_WATCH_XAUTHORITY="$HOME/.Xauthority"
        else
            TECH_WATCH_XAUTHORITY=/dev/null
        fi
    fi
    mkdir -p "$TECH_WATCH_CONFIG_DIR" "$TECH_WATCH_DATA_DIR"
    export TECH_WATCH_UID TECH_WATCH_GID
    export TECH_WATCH_CONFIG_DIR TECH_WATCH_DATA_DIR
    export TECH_WATCH_RUNTIME_DIR TECH_WATCH_XAUTHORITY TECH_WATCH_KDE_GLOBALS
    export DISPLAY="${DISPLAY:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}"
    export XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-}"
    export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-KDE}"
    export XDG_SESSION_DESKTOP="${XDG_SESSION_DESKTOP:-plasma}"
    export DESKTOP_SESSION="${DESKTOP_SESSION:-plasma}"
    export KDE_FULL_SESSION="${KDE_FULL_SESSION:-true}"
    export KDE_SESSION_VERSION="${KDE_SESSION_VERSION:-6}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}"
    export TECH_WATCH_QT_PLATFORM="${TECH_WATCH_QT_PLATFORM:-}"
    export TECH_WATCH_INSTALL_AGY="${TECH_WATCH_INSTALL_AGY:-1}"
}

compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        docker compose -p tech-watch -f "$COMPOSE_FILE" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose -p tech-watch -f "$COMPOSE_FILE" "$@"
    else
        return 127
    fi
}

install_desktop_entry() {
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local apps_dir="$data_home/applications"
    local icon_root="$data_home/icons/hicolor"
    local size src exec_args=""
    if [[ "$APP_MODE" == "container" ]]; then
        exec_args=" --container-app"
    fi
    mkdir -p "$apps_dir"
    for size in 16 24 32 48 64 128 256; do
        src="$ROOT/tech_watch/resources/tech-watch-$size.png"
        [[ -f "$src" ]] || continue
        install -Dm644 "$src" "$icon_root/${size}x${size}/apps/tech-watch.png"
    done
    cat > "$apps_dir/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tech Watch Tray
Comment=Daily AI technicals monitor for securities
Exec="$ROOT/start.sh"$exec_args
Icon=tech-watch
Terminal=false
Categories=Utility;Finance;
StartupWMClass=$APP_ID
EOF
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$icon_root" >/dev/null 2>&1 || true
    fi
}

if [[ "$DOWN" == 1 ]]; then
    if ! compose_cmd version >/dev/null 2>&1; then
        echo "docker compose is not available" >&2
        exit 1
    fi
    docker rm -f tech-watch-app >/dev/null 2>&1 || true
    compose_cmd down
    exit 0
fi

if [[ "$APP_MODE" == "container" ]]; then
    PY="$PYTHON_BIN"
elif [[ "$INSTALL" == 1 ]]; then
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        echo "Creating virtualenv at $VENV_DIR..."
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    if ! "$VENV_DIR/bin/python" -c "import PyQt6, pandas, yfinance" >/dev/null 2>&1; then
        echo "Installing Python dependencies..."
        (
            cd "$ROOT"
            "$VENV_DIR/bin/python" -m pip install --upgrade pip
            "$VENV_DIR/bin/python" -m pip install -e '.[screener]'
        )
    fi
    PY="$VENV_DIR/bin/python"
elif [[ -x "$VENV_DIR/bin/python" ]]; then
    PY="$VENV_DIR/bin/python"
else
    PY="$PYTHON_BIN"
fi

if [[ -z "${OLLAMA_MODEL:-}" ]]; then
    OLLAMA_MODEL="$(
        cd "$ROOT"
        PYTHONPATH="$ROOT" "$PY" -c \
            'from tech_watch.config import load_config; print((load_config().get("ai") or {}).get("ollama_model") or "llama3.2:1b")' \
            2>/dev/null || echo "llama3.2:1b"
    )"
fi

if [[ "$APP_MODE" == "container" && "$MODE" == auto ]]; then
    MODE=container
fi

if [[ "$MODE" == auto ]]; then
    if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
        MODE=host
    else
        MODE=container
    fi
fi

if [[ "$MODE" == container ]] && ! compose_cmd version >/dev/null 2>&1; then
    if command -v ollama >/dev/null 2>&1; then
        echo "docker compose is unavailable; falling back to host Ollama." >&2
        MODE=host
    else
        echo "docker compose is unavailable and host Ollama was not found; continuing without Ollama fallback." >&2
        MODE=off
    fi
fi

if [[ "$APP_MODE" == "container" ]]; then
    setup_container_app_env
    if [[ "$MODE" == host ]]; then
        export TECH_WATCH_OLLAMA_HOST="${TECH_WATCH_OLLAMA_HOST:-http://host.docker.internal:11434}"
    else
        export TECH_WATCH_OLLAMA_HOST="${TECH_WATCH_OLLAMA_HOST:-http://ollama:11434}"
    fi
fi

if [[ "$MODE" == container ]]; then
    if ! docker info >/dev/null 2>&1; then
        echo "Docker daemon is not available." >&2
        exit 1
    fi

    echo "Starting Ollama container..."
    compose_cmd up -d ollama

    ready=0
    for _ in {1..60}; do
        if compose_cmd exec -T ollama ollama list >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [[ "$ready" != 1 ]]; then
        echo "Ollama container did not become ready." >&2
        compose_cmd logs --tail=50 ollama >&2 || true
        exit 1
    fi

    if [[ "$PULL" == 1 ]] && ! compose_cmd exec -T ollama ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
        echo "Pulling Ollama model $OLLAMA_MODEL..."
        compose_cmd exec -T ollama ollama pull "$OLLAMA_MODEL"
    fi

    if [[ "$APP_MODE" == "host" ]]; then
        export PATH="$ROOT/scripts/ollama-wrapper:$PATH"
    fi
    echo "Using containerized Ollama ($OLLAMA_MODEL)."
elif [[ "$MODE" == host ]]; then
    if ! command -v ollama >/dev/null 2>&1; then
        echo "Host Ollama was requested but the ollama command was not found." >&2
        exit 1
    fi
    if [[ "$PULL" == 1 ]] && ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
        echo "Pulling Ollama model $OLLAMA_MODEL..."
        ollama pull "$OLLAMA_MODEL" || echo "Could not pull $OLLAMA_MODEL; continuing." >&2
    fi
    echo "Using host Ollama ($OLLAMA_MODEL)."
else
    echo "Ollama fallback disabled."
fi

if [[ "$SMOKE" == 1 ]]; then
    if [[ "$MODE" == container ]]; then
        echo "Testing containerized Ollama..."
        compose_cmd exec -T ollama ollama run "$OLLAMA_MODEL" "Reply with exactly: OK"
        echo "Ollama smoke test passed."
    elif [[ "$MODE" == host ]]; then
        echo "Testing host Ollama..."
        ollama run "$OLLAMA_MODEL" "Reply with exactly: OK"
        echo "Ollama smoke test passed."
    else
        echo "Ollama is disabled; skipping Ollama smoke test."
    fi

    if [[ "$APP_MODE" == container ]]; then
        if ! docker info >/dev/null 2>&1; then
            echo "Docker daemon is not available." >&2
            exit 1
        fi
        echo "Building Tech Watch app container..."
        compose_cmd build app
        echo "Testing Tech Watch app container..."
        compose_cmd run --rm -T --no-deps --entrypoint python app -c \
            "import PyQt6, pandas, yfinance; import tech_watch.app; print('Tech Watch app container OK')"
        echo "App container smoke test passed."
    fi
    exit 0
fi

install_desktop_entry

if [[ "$APP_MODE" == "container" ]]; then
    if ! docker info >/dev/null 2>&1; then
        echo "Docker daemon is not available." >&2
        exit 1
    fi
    echo "Building Tech Watch app container..."
    compose_cmd build app
    echo "Starting Tech Watch Tray in container..."
    docker rm -f tech-watch-app >/dev/null 2>&1 || true
    if [[ "$MODE" == container ]]; then
        compose_cmd run --rm -T --name tech-watch-app app "${APP_ARGS[@]}"
        exit $?
    fi
    compose_cmd run --rm -T --name tech-watch-app --no-deps app "${APP_ARGS[@]}"
    exit $?
fi

echo "Starting Tech Watch Tray..."
exec "$PY" "$ROOT/tech-watch.py" "${APP_ARGS[@]}"
