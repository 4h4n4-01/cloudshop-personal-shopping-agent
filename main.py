"""CloudShop data-pipeline entry point (bronze -> silver -> gold).

    python main.py

Rebuilds `data/datamart/` from `data/source/demo_catalog.json` and prints a summary. The web
app and the evaluation read the gold catalog this produces; they also rebuild it automatically
if it is missing, so running this by hand is optional.
"""
from pipeline import bronze, gold, silver


def main() -> None:
    print("CloudShop data pipeline  (bronze -> silver -> gold)")
    raw = bronze.ingest()
    print(f"  bronze  ingested {len(raw)} raw demo products")

    clean, report = silver.clean(raw)
    print(f"  silver  kept {report['kept']}/{report['in']} "
          f"(dropped {len(report['dropped'])}, stripped {report['vocab_tokens_stripped']} out-of-vocab tokens)")
    for d in report["dropped"]:
        print(f"            - dropped {d['id']}: {d['reason']}")

    catalog, stock = gold.build(clean)
    gold.write(catalog, stock, report)
    in_stock = sum(p["in_stock"] for p in catalog)
    by_cat = {}
    for p in catalog:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    print(f"  gold    wrote {len(catalog)} products ({in_stock} in stock) + stock store")
    print(f"          categories: {dict(sorted(by_cat.items()))}")
    print("Done. Gold catalog -> data/datamart/gold/catalog.json")


if __name__ == "__main__":
    main()
