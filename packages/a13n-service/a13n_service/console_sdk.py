"""Export the installed Console bridge declarations for an App project."""

import argparse
from importlib.resources import files
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Path to the generated TypeScript declaration file")
    destination = parser.parse_args().destination
    source = files("a13n_service").joinpath("static", "console-sdk", "index.d.ts")
    if not source.is_file():
        parser.error("Console SDK declarations are missing; install a published Service wheel or build Console assets")
    declarations = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(declarations)


if __name__ == "__main__":
    main()
