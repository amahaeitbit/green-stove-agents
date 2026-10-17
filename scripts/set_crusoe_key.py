"""Ask for the Crusoe API key in a native macOS dialog and write it into .env.

The key never appears in the terminal, the chat or git. Prints only a status line.
Usage: .venv/bin/python scripts/set_crusoe_key.py
"""
import re
import subprocess
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"
SCRIPT = ('display dialog "Paste your Crusoe API key (Intelligence Foundry → API keys)" '
          'default answer "" with hidden answer with title "Green-Stove Agents" buttons {"Cancel","Save"} '
          'default button "Save"')


def main() -> int:
    try:
        out = subprocess.run(["osascript", "-e", SCRIPT, "-e", "text returned of result"],
                             capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        print("cancelled; .env unchanged")
        return 1
    key = out.strip()
    if len(key) < 20 or " " in key:
        print(f"that does not look like an API key ({len(key)} characters); .env unchanged")
        return 1
    if not ENV.exists():
        example = ENV.with_name(".env.example")
        ENV.write_text(example.read_text() if example.exists() else "")
    text = ENV.read_text()
    line = "CRUSOE_API_KEY=" + key
    text = re.sub(r"(?m)^CRUSOE_API_KEY=.*$", lambda _: line, text) if "CRUSOE_API_KEY=" in text else text + f"\n{line}\n"
    ENV.write_text(text)
    print(f"saved CRUSOE_API_KEY ({len(key)} characters) to .env; set GREENSTOVE_LIVE_SPEND_OK=1 for live runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
