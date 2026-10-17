"""Ask for the Crusoe API key in a native macOS dialog and write it into .env.

The key never appears in the terminal, the chat or git. Prints only a status line.
Usage: .venv/bin/python scripts/set_crusoe_key.py [--enable-live] [--var YDC_API_KEY]
  --var stores a different secret (for example the You.com key) with the same dialog.
  --enable-live also sets GREENSTOVE_LLM=crusoe and GREENSTOVE_LIVE_SPEND_OK=1 (billable calls).
"""
import re
import subprocess
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"
VAR = sys.argv[sys.argv.index("--var") + 1] if "--var" in sys.argv else "CRUSOE_API_KEY"
PROMPT = {"CRUSOE_API_KEY": "Paste your Crusoe API key (Intelligence Foundry → API keys)",
          "YDC_API_KEY": "Paste your You.com API key (you.com/platform/api-keys)"}.get(VAR, f"Paste {VAR}")
SCRIPT = (f'display dialog "{PROMPT}" '
          'default answer "" with hidden answer with title "Green-Stove Agents" buttons {"Cancel","Save"} '
          'default button "Save"')


def set_var(text: str, name: str, value: str) -> str:
    line = f"{name}={value}"
    if re.search(rf"(?m)^{name}=", text):
        return re.sub(rf"(?m)^{name}=.*$", lambda _: line, text)
    return text.rstrip("\n") + f"\n{line}\n"


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
    text = set_var(ENV.read_text(), VAR, key)
    live = "--enable-live" in sys.argv
    if live:
        text = set_var(set_var(text, "GREENSTOVE_LLM", "crusoe"), "GREENSTOVE_LIVE_SPEND_OK", "1")
    ENV.write_text(text)
    print(f"saved {VAR} ({len(key)} characters) to .env"
          + ("; live mode on (GREENSTOVE_LLM=crusoe, GREENSTOVE_LIVE_SPEND_OK=1)" if live
             else "; rerun with --enable-live to turn on billable calls"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
