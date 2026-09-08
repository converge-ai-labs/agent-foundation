from importlib.metadata import version

import a13n_envd_client


def test_package_version_matches_distribution() -> None:
    assert a13n_envd_client.__version__ == version("a13n-envd-client")
