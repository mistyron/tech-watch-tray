# syntax=docker/dockerfile:1
FROM ollama/ollama:latest AS ollama

FROM python:3.13-slim-trixie

ARG UID=1000
ARG GID=1000
ARG INSTALL_AGY=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_PLUGIN_PATH=/usr/lib/x86_64-linux-gnu/qt6/plugins \
    HOME=/home/techwatch \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data \
    XDG_CACHE_HOME=/cache \
    PATH=/home/techwatch/.local/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        breeze-cursor-theme \
        breeze-icon-theme \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-noto-mono \
        kde-style-breeze \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libgl1 \
        libglib2.0-0 \
        libice6 \
        libsm6 \
        libwayland-client0 \
        libwayland-cursor0 \
        libwayland-egl1 \
        libx11-xcb1 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-sync1 \
        libxcb-xfixes0 \
        libxcb-xinerama0 \
        libxcb-xkb1 \
        libxkbcommon-x11-0 \
        libxkbcommon0 \
        plasma-integration \
        tar \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ollama /bin/ollama /usr/local/bin/ollama

RUN groupadd --gid "${GID}" techwatch 2>/dev/null || true \
    && useradd --uid "${UID}" --gid "${GID}" --create-home \
        --home-dir /home/techwatch --shell /bin/bash techwatch 2>/dev/null || true \
    && mkdir -p /app /config /data /cache /home/techwatch \
    && chown -R "${UID}:${GID}" /app /config /data /cache /home/techwatch

WORKDIR /app

COPY pyproject.toml README.md tech-watch.py ./
COPY tech_watch ./tech_watch
COPY docker/entrypoint.sh /usr/local/bin/tech-watch-entrypoint

RUN pip install --upgrade pip \
    && pip install 'PyQt6==6.8.1' \
    && pip install '.[screener]' \
    && chmod +x /usr/local/bin/tech-watch-entrypoint

RUN if [ "${INSTALL_AGY}" = "1" ]; then \
        echo "Installing Antigravity CLI (agy)..." \
        && curl -fsSL https://antigravity.google/cli/install.sh \
            | bash -s -- --dir /usr/local/bin; \
    fi

ENTRYPOINT ["/usr/local/bin/tech-watch-entrypoint"]
