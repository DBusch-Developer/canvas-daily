"""Run pytest with color forced on and render the output as a terminal-styled HTML page.

The HTML is what the browser screenshots into a PNG, giving us a faithful,
colored picture of a test run: red for failures, green for passes.

Usage:
    python tools/run_to_html.py <label> [pytest args...]

Example:
    python tools/run_to_html.py dates-green tests/test_dates.py
    python tools/run_to_html.py dates-red   tests/test_dates.py

Writes docs/test-evidence/<label>.html. The exit code is pytest's own, so
the harness can tell red from green, but we never let a red run abort the render.
"""

import subprocess
import sys
from pathlib import Path

from ansi2html import Ansi2HTMLConverter

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "test-evidence"


def run_pytest(pytest_args: list[str]) -> tuple[str, int]:
    """Run pytest with ANSI color forced on, capturing combined output."""
    cmd = [sys.executable, "-m", "pytest", "--color=yes", *pytest_args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout + proc.stderr, proc.returncode


def to_html(label: str, raw_output: str) -> str:
    """Wrap the colored pytest output in a small terminal-styled page."""
    conv = Ansi2HTMLConverter(dark_bg=True, scheme="xterm", line_wrap=False)
    # full=False yields the colored spans WITHOUT a wrapping element, so we wrap
    # them ourselves in <pre class="term"> below. The default text color lives on
    # that wrapper — otherwise un-colored text falls back to the browser default
    # (black) and vanishes against the dark background.
    body = conv.convert(raw_output, full=False)
    styles = conv.produce_headers()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{label}</title>
{styles}
<style>
  html, body {{ margin: 0; background: #0c0c0c; }}
  .frame {{ padding: 24px 28px; }}
  .titlebar {{
    font: 600 13px/1.4 -apple-system, Segoe UI, sans-serif;
    color: #cdcdcd; background: #1e1e1e;
    padding: 10px 16px; border-radius: 8px 8px 0 0;
    border-bottom: 1px solid #333;
  }}
  .titlebar .dot {{ display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:7px; vertical-align:middle; }}
  .dot.r {{ background:#ff5f56; }} .dot.y {{ background:#ffbd2e; }} .dot.g {{ background:#27c93f; }}
  pre.term {{
    display: block;  /* override ansi2html's inline default so padding/bg apply */
    margin: 0; padding: 18px 16px 22px;
    background: #0c0c0c; border-radius: 0 0 8px 8px;
    color: #d4d4d4;  /* default for un-colored text; red/green spans keep their own color */
    font: 14px/1.5 "Cascadia Code", "Consolas", ui-monospace, monospace;
    white-space: pre; overflow-x: auto;
  }}
</style>
</head>
<body>
  <div class="frame">
    <div class="titlebar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>pytest — {label}</div>
    <pre class="term">{body}</pre>
  </div>
</body>
</html>"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    label = sys.argv[1]
    pytest_args = sys.argv[2:]

    raw, code = run_pytest(pytest_args)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / f"{label}.html"
    out.write_text(to_html(label, raw), encoding="utf-8")

    status = "GREEN (all passed)" if code == 0 else f"RED (pytest exit {code})"
    print(f"[{status}] wrote {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
