"""Import smoke tests."""


def test_package_imports() -> None:
    """The package should be importable from the src layout."""
    import ozon_similar_products

    assert ozon_similar_products.__version__
