"""Bronze layer: raw ingestion.

Like the MLE pipeline's bronze stage, this copies each source verbatim into the datamart with
no transformation, plus a small manifest (where it came from, how many rows, when). The demo
catalog is ingested here from `data/source/`; live-store feeds are the other bronze source and
are written under `datamart/bronze/live/` at runtime by the store connectors (see
`app/stores/common.py`).
"""
import json
import time

from app.config import BRONZE_DIR, DEMO_SOURCE


def ingest() -> list[dict]:
    """Copy the demo source into bronze verbatim and return the raw rows."""
    raw = json.loads(DEMO_SOURCE.read_text(encoding="utf-8"))
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    (BRONZE_DIR / "demo_catalog.json").write_text(
        json.dumps(raw, indent=2), encoding="utf-8")
    (BRONZE_DIR / "_manifest.json").write_text(json.dumps({
        "source": str(DEMO_SOURCE.name),
        "rows": len(raw),
        "ingested_at": time.time(),
    }, indent=2), encoding="utf-8")
    return raw
