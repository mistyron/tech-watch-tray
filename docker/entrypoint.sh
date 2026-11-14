#!/usr/bin/env bash
set -euo pipefail

: "${XDG_RUNTIME_DIR:=/tmp/tech-watch-runtime}"
: "${XDG_CONFIG_HOME:=/config}"
: "${XDG_DATA_HOME:=/data}"
: "${XDG_CACHE_HOME:=/cache}"
: "${HOME:=/home/techwatch}"
export XDG_RUNTIME_DIR XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME HOME

target_uid="${TECH_WATCH_UID:-1000}"
target_gid="${TECH_WATCH_GID:-1000}"

if [[ "$(id -u)" == "0" && "$target_uid" != "0" ]]; then
    mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$HOME"
    if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
        mkdir -m 700 -p "$XDG_RUNTIME_DIR"
    fi
    chown -R "${target_uid}:${target_gid}" \
        "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$HOME" 2>/dev/null || true
    chown "${target_uid}:${target_gid}" "$XDG_RUNTIME_DIR" 2>/dev/null || true
    chown "${target_uid}:${target_gid}" /usr/local/bin/agy 2>/dev/null || true
    exec setpriv --reuid="${target_uid}" --regid="${target_gid}" --clear-groups \
        /usr/local/bin/tech-watch-entrypoint "$@"
fi

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

if [[ -z "${WAYLAND_DISPLAY:-}" && -S "${XDG_RUNTIME_DIR}/wayland-0" ]]; then
    export WAYLAND_DISPLAY=wayland-0
fi

if [[ -z "${QT_QPA_PLATFORMTHEME:-}" && "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then
    export QT_QPA_PLATFORMTHEME=kde
fi

if [[ -n "${TECH_WATCH_QT_PLATFORM:-}" ]]; then
    export QT_QPA_PLATFORM="${TECH_WATCH_QT_PLATFORM}"
elif [[ -z "${QT_QPA_PLATFORM:-}" ]]; then
    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        export QT_QPA_PLATFORM=wayland
    elif [[ -n "${DISPLAY:-}" ]]; then
        export QT_QPA_PLATFORM=xcb
    fi
fi

exec python -m tech_watch "$@"
