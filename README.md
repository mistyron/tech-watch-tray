# Tech Watch Tray (KDE/Plasma)

A small Linux tray application for KDE/Plasma that monitors stock/security technicals by asking an **AI CLI/command** once per day (or on demand) and notifying you when a configured security looks positive for adding capital.

## What it does

- Runs in the system tray (`QSystemTrayIcon` / Plasma tray).
- Tracks a configurable list of securities (default: `XIC.TO`).
- Checks **all configured securities automatically every 24 hours** (configurable) while running, and also supports manual “Check all now”. On startup it checks anything due, or schedules the next auto-check for the remaining interval.
- Parses a simple `VERDICT`/`SUMMARY` from the response.
- Shows a desktop notification when a verdict matches your alert set (default: `BUY`).
- Lets you view the full response per symbol from the tray menu.

## Requirements

- Linux KDE/Plasma session with system tray support.
- Python 3.10+.
- `PyQt6`.
- Optional, for the TSX screener and Ollama live-data fallback: `pip install -e '.[screener]'`
  (numpy, pandas, requests, yfinance, lxml).
- A working **AI command** that can answer a prompt from the CLI. Default config tries:

  ```bash
  gemini -p "<prompt>"
  ```

  If your `gemini` CLI no longer works for your account (see troubleshooting), change `ai.command` in the config or use the tray menu: **Settings...** The command must accept the prompt; use `{prompt}` as a placeholder. Example: `agy -p {prompt}` (if you have an Antigravity CLI installed), or any script/wrapper that prints the model response to stdout.

## Run

From this directory:

```bash
python3 -m tech_watch
# or
python3 tech-watch.py
# optional immediate check:
python3 -m tech_watch --check-now
```

## Configuration

Config is stored at:

- `$XDG_CONFIG_HOME/tech-watch/config.json` (usually `~/.config/tech-watch/config.json`)

Tray menu actions: **Check all now**, **Cancel check** (while a check is running), **Manage securities...** (add/remove symbols and set the check interval), **Settings...** (AI command, prompt, notifications, and other options), **TSX Screener...**, and **Open config file location**.

Important config fields:

- `symbols`: list of tickers/symbols (e.g., `["XIC.TO", "AAPL"]`).
- `check_interval_hours`: how often to run checks.
- `ai.command`: argv list for the AI CLI/command. Use `{prompt}` (and optionally `{symbol}`). Example:
  ```json
  "ai": { "command": ["gemini", "-p", "{prompt}"], "timeout_seconds": 180 }
  ```
- `query_template`: prompt sent to the AI. Must include `{symbol}`. The default asks for:
  - `VERDICT: BUY|WAIT|HOLD|SELL`
  - `SUMMARY: <one short sentence>`
- `notify_on`: verdicts that trigger notifications (default: `["BUY"]`).
- `last_results`: cached latest result per symbol (written automatically).

## Troubleshooting

Logs are written to `$XDG_DATA_HOME/tech-watch/tech-watch.log` (usually `~/.local/share/tech-watch/tech-watch.log`) and rotate at 1 MB with three backups.

### `IneligibleTierError` / "Gemini Code Assist for individuals ... migrate to Antigravity"

This error comes from the **Gemini CLI** (`gemini`), not from Tech Watch. Your `~/.config/tech-watch/config.json` still points to `gemini` unless you change `ai.command`, and a running tray app must be **restarted** to pick up code/config changes.

Fix options:
1. **Antigravity CLI (`agy`)** — Google’s migration path; official Linux install (Fedora supported): `curl -fsSL https://antigravity.google/cli/install.sh | bash` (installs to `~/.local/bin/agy`). Verify with `agy --version` and a prompt test, then set `ai.command` to e.g. `["agy", "-p", "{prompt}"]` (confirm flags with `agy --help`). Review the installer/docs before running any remote script: https://antigravity.google/docs/cli/install
2. **`aichat` / OpenRouter or another configured provider** — if you already have `aichat` working, set `ai.command` to a non-streaming command that prints the response, e.g.:
   ```json
   "ai": { "command": ["/usr/local/sbin/aichat", "-S", "-m", "openrouter:openrouter/free", "{prompt}"] }
   ```
   Use `-S` (no stream). OpenRouter free models change/ratelimit; `openrouter/free` routes to an available free model but quality/availability varies — test: `aichat -S -m openrouter:openrouter/free "Reply with OK"`.
3. **API-based wrapper script**: a small script that calls a provider you’re authorized to use, prints model text to stdout, configured as `["/path/to/wrapper.sh","{prompt}"]`. Keep API keys out of shared config/repos.

## Autostart on Plasma login (optional)

Create `~/.config/autostart/tech-watch.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Tech Watch Tray
Comment=Daily AI technicals monitor for securities
Exec=/usr/bin/python3 /opt/src/tech-watch/tech-watch.py
X-KDE-autostart-after=panel
Terminal=false
Categories=Utility;Finance;
```

Adjust `Exec` if you move the project.

## Notes / limitations

- Normal checks rely on the configured AI command/provider to analyze/report technicals for the symbol you provide; results depend on the provider's available data/tools and may be incomplete or stale. The optional Ollama fallback and TSX screener fetch Yahoo Finance data directly (adjusted prices).
- The live snapshot tries the bare symbol first, then common Canadian suffixes such as `.TO`, which supports both US and TSX symbols.
- Treat output as informational, not financial advice.
- AI prompts can take time and require network/auth; failures are shown in notifications and stored per symbol.
- For unattended daily checks, make sure your configured `ai.command` works non-interactively in a terminal.
