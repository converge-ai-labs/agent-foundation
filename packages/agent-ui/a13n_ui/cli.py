from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Literal

from a13n_logging import configure_logging

from a13n_ui import tui, webui

Surface = Literal["webui", "tui"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a13n-ui")
    parser.add_argument(
        "surface",
        nargs="?",
        choices=("webui", "tui"),
        default="webui",
        help="presentation surface to run (default: webui)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    configure_logging(logger_names=("a13n_ui",))
    surface: Surface = args.surface
    if surface == "tui":
        tui.run()
        return
    webui.run()
