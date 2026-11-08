# Tech Watch (KDE/Plasma tray)

A small Linux tray application for KDE/Plasma that monitors stock/security technicals by asking an **AI CLI/command** once per day (or on demand) and notifying you when a configured security looks positive for adding capital.

## What it does

- Runs in the system tray (`QSystemTrayIcon` / Plasma tray).
- Tracks a configurable list of securities (defaults: `CGL.TO`, `HURA.TO`).
- Asks an AI command in the background on a configurable interval (default: every 24 hours).
- Parses a simple `VERDICT`/`SUMMARY` from the response.
- Shows a desktop notification when a verdict matches your alert set (default: `BUY`).
- Lets you view the full response per symbol from the tray menu.

## Requirements

- Linux KDE/Plasma session with system tray support.
- Python 3.10+.
- `PyQt6`.
- A working **AI command** that can answer a prompt from the CLI. Default config tries:

  ```bash
  gemini -p "<prompt>"
  ```

  If your `gemini` CLI no longer works for your account (see troubleshooting), change `ai.command` in the config or use the tray menu: **Set AI command...** The command must accept the prompt; use `{prompt}` as a placeholder. Example: `agy -p {prompt}` (if you have an Antigravity CLI installed), or any script/wrapper that prints the model response to stdout.

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

Tray menu actions: **Manage securities...** (add/remove symbols, interval), **Set AI command...**.

Important config fields:

- `symbols`: list of tickers/symbols (e.g., `["CGL.TO", "HURA.TO", "AAPL"]`).
- `check_interval_hours`: how often to run checks.
- `ai.command`: argv list for the AI CLI/command. Use `{prompt}` (and optionally `{symbol}`). Example:
  ```json
  "ai": { "command": ["gemini", "-p", "{prompt}"], "timeout_seconds": 180 }
  ```
- `gemini_command` / `gemini_args`: legacy fallback (used only if `ai.command` is missing/invalid).
- `query_template`: prompt sent to the AI. Must include `{symbol}`. The default asks for:
  - `VERDICT: BUY|WAIT|HOLD|SELL`
  - `SUMMARY: <one short sentence>`
- `notify_on`: verdicts that trigger notifications (default: `["BUY"]`).
- `last_results`: cached latest result per symbol (written automatically).

## Troubleshooting

### `IneligibleTierError` / "Gemini Code Assist for individuals ... migrate to Antigravity"

This error comes from the **Gemini CLI** (`gemini`), not from Tech Watch. Tech Watch shells out to the configured command; if that command fails, the tray shows its error.

Fix options:
1. **Use a working Gemini-capable CLI for your account** (e.g. Antigravity's CLI if you have it installed) and set `ai.command` accordingly via **Set AI command...** or in `~/.config/tech-watch/config.json`. Test the command in a terminal first, e.g. `your-command -p "Reply with OK"`.
2. **Use an API-based wrapper script**: create a small script that calls the Gemini API/provider you have access to, prints the model text to stdout, and configure `ai.command` to run it (e.g. `["/path/to/my-gemini-query.sh", "{prompt}"]`). Keep API keys out of the repo/config if the file is shared.
3. Re-auth/update your Gemini CLI setup per Google's current guidance: https://antigravity.google (availability depends on your account/product). Tech Watch will work again once `gemini -p "hello"` succeeds.

## Autostart on Plasma login (optional)

Create `~/.config/autostart/tech-watch.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Tech Watch
Comment=Daily Gemini technicals monitor
Exec=/usr/bin/python3 /opt/src/tech-watch/tech-watch.py
X-KDE-autostart-after=panel
Terminal=false
Categories=Utility;Finance;
```

Adjust `Exec` if you move the project.

## Notes / limitations

- This app does **not** fetch market data directly; it relies on Gemini's ability to analyze/report technicals for the symbol you provide. Results depend on Gemini's available data/tools and may be incomplete or stale.
- Treat output as informational, not financial advice.
- Gemini CLI prompts can take time and require network/auth; failures are shown in notifications and stored per symbol.
- For unattended daily checks, make sure Gemini CLI works non-interactively in your environment (`gemini -p "hello"`).
