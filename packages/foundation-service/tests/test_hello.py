from importlib.metadata import version

from converge_foundation_service import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("converge-foundation-service")
