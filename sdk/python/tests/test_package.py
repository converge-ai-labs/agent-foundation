from importlib.metadata import version

import a13n_sdk


def test_package_version_matches_distribution() -> None:
    assert a13n_sdk.__version__ == version("a13n-sdk")
