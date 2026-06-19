"""Thin wrapper for package CLI entrypoint."""

from ozon_similar_products.cli.run_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
