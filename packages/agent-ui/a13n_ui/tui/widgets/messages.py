"""Presentation-only Textual messages carrying typed terminal intents."""

from __future__ import annotations

from textual.message import Message

from a13n_ui.tui.intents import TerminalIntent


class IntentRequested(Message):
    """Request that the terminal controller handle one typed user intent."""

    def __init__(self, intent: TerminalIntent) -> None:
        super().__init__()
        self.intent = intent


__all__ = ["IntentRequested"]
