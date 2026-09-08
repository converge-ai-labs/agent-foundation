from importlib.metadata import version

import a13n


def test_package_version_matches_distribution() -> None:
    assert a13n.__version__ == version("a13n")
