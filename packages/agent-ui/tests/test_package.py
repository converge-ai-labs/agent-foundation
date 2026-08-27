from importlib.metadata import version

from a13n_ui import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("a13n-ui")
