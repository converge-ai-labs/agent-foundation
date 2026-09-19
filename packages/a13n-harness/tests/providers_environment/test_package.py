from importlib.metadata import version

from a13n_harness import __version__


def test_environment_ships_in_harness():
    assert __version__ == version("a13n-harness")
