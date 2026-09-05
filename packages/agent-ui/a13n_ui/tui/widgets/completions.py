"""Bounded composer completion popup for Project paths and exact Skills."""

from __future__ import annotations

import hashlib

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import ListItem, ListView, Static

from a13n_ui.surfaces import SkillReference
from a13n_ui.tui.intents import ApplyCompletion
from a13n_ui.tui.models import CompletionState
from a13n_ui.tui.widgets.messages import IntentRequested


class CompletionPopup(Container):
    def __init__(self) -> None:
        super().__init__(id="completion-popup")
        self._actions: dict[str, ApplyCompletion] = {}
        self._signature: tuple[object, ...] | None = None

    def compose(self) -> ComposeResult:
        yield Static("Completions", id="completion-title", markup=False)
        yield ListView(id="completion-list")

    async def project(self, completion: CompletionState) -> None:
        list_view = self.query_one("#completion-list", ListView)
        widgets: list[ListItem] = []
        if completion.kind == "path":
            paths = () if completion.paths is None else completion.paths.items
            signature = (
                completion.request_version,
                tuple((item.display, item.kind) for item in paths),
            )
            if self._signature == signature:
                return
            self._signature = signature
            await list_view.clear()
            self._actions.clear()
            for index, item in enumerate(paths):
                item_id = _item_id(index, item.display)
                replacement = (
                    "@" + item.display + ("/" if item.kind == "directory" and not item.display.endswith("/") else "")
                )
                self._actions[item_id] = ApplyCompletion(
                    key=completion.key,
                    token_start=completion.token_start,
                    token_end=completion.token_end,
                    replacement=replacement,
                    project_path=item.display,
                )
                widgets.append(ListItem(Static(f"{item.display}  {item.kind}", markup=False), id=item_id))
        else:
            catalog = completion.skills
            items = () if catalog is None else catalog.items
            query = completion.query.casefold()
            skills = tuple(
                item for item in items if item.name.casefold().startswith(query) or query in item.name.casefold()
            )[:50]
            signature = (
                completion.request_version,
                tuple((item.item_id, item.name) for item in skills),
            )
            if self._signature == signature:
                return
            self._signature = signature
            await list_view.clear()
            self._actions.clear()
            if catalog is not None:
                for index, item in enumerate(skills):
                    item_id = _item_id(index, item.name)
                    self._actions[item_id] = ApplyCompletion(
                        key=completion.key,
                        token_start=completion.token_start,
                        token_end=completion.token_end,
                        replacement=f"${item.name}",
                        skill_reference=SkillReference(
                            catalog_id=catalog.catalog_id,
                            item_id=item.item_id,
                            name=item.name,
                        ),
                    )
                    widgets.append(ListItem(Static(f"${item.name}\n  {item.description}", markup=False), id=item_id))
        if widgets:
            await list_view.extend(widgets)
            list_view.index = 0
        else:
            await list_view.append(ListItem(Static("No matching completions.", markup=False), disabled=True))

    def move_selection(self, direction: int) -> None:
        list_view = self.query_one("#completion-list", ListView)
        if direction > 0:
            list_view.action_cursor_down()
        else:
            list_view.action_cursor_up()

    def accept_selection(self) -> None:
        self.query_one("#completion-list", ListView).action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id is None:
            return
        action = self._actions.get(item_id)
        if action is not None:
            self.post_message(IntentRequested(action))


def _item_id(index: int, value: str) -> str:
    return f"completion-item-{index}-{hashlib.sha256(value.encode()).hexdigest()[:10]}"


__all__ = ["CompletionPopup"]
