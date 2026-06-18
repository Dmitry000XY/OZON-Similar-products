"""Thin wrapper for package CLI entrypoint."""

from ozon_similar_products.cli.preview_recommendations import main

if __name__ == "__main__":
    raise SystemExit(main())
