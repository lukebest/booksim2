#!/usr/bin/env python3
"""Generate HTML report: control-theoretic formalization of NoC dynamic latency.

Usage:
  python3 utils/gen_control_latency_report.py
  → results/report_control_dynamic_latency.html
"""

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "report_control_dynamic_latency.html"
TEMPLATE = ROOT / "utils" / "templates" / "report_control_dynamic_latency.html"


def main() -> None:
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not TEMPLATE.exists():
        raise SystemExit(f"missing template: {TEMPLATE}")
    text = TEMPLATE.read_text(encoding="utf-8").replace("@@GEN@@", gen)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
