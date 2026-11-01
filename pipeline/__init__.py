"""Medallion data pipeline for CloudShop: bronze -> silver -> gold.

Mirrors the MLE Assignment-1 `utils/` pipeline. Each layer is one module:
  * bronze - raw ingestion, copied verbatim from `data/source/`
  * silver - cleaning, typing and controlled-vocabulary standardisation
  * gold   - the query-ready serving catalog + a per-size stock store the app reads

Run the whole thing with `python main.py`, or call `build_demo()` (the app does this
automatically if the gold catalog is missing).
"""
from . import bronze, gold, silver


def build_demo() -> list[dict]:
    """Rebuild datamart/ from the demo source and return the gold catalog."""
    raw = bronze.ingest()
    clean, report = silver.clean(raw)
    catalog, stock = gold.build(clean)
    gold.write(catalog, stock, report)
    return catalog
