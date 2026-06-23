from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from server import fetch_prices


ROOT = Path(__file__).resolve().parent
OUTPUT_FILES = {
    "5m": ROOT / "data-5m.json",
    "15m": ROOT / "data-15m.json",
    "daily": ROOT / "data-daily.json",
    "weekly": ROOT / "data-weekly.json",
    "monthly": ROOT / "data-monthly.json",
}


def build_payload(tf: str, shared_sizes: dict | None = None) -> dict:
    data = fetch_prices(tf)
    if shared_sizes is not None:
        data["__sizes__"] = shared_sizes
        data.setdefault("__meta__", {})
        data["__meta__"]["size_weight_note"] = (
            "Estimated from constituent market caps in the loaded SET50 universe."
        )

    return {
        "ok": True,
        "data": data,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": tf,
        "source": "yfinance",
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"[generate] wrote {path.name}")


def main() -> None:
    daily_payload = build_payload("daily")
    shared_sizes = daily_payload["data"].get("__sizes__", {})
    write_json(OUTPUT_FILES["daily"], daily_payload)

    for tf in ("5m", "15m", "weekly", "monthly"):
        payload = build_payload(tf, shared_sizes=shared_sizes)
        write_json(OUTPUT_FILES[tf], payload)


if __name__ == "__main__":
    main()
