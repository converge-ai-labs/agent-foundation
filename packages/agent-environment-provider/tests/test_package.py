from importlib.metadata import version

from converge_agent_environment_provider import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("converge-agent-environment-provider")
