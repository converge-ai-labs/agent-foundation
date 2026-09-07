"""Canonical approval facts from one published Environment file selection."""

from a13n_harness.environment.providers import FileScopeSelection
from a13n_harness.tools.metadata import CanonicalResource


def selection_resource(selection: FileScopeSelection, *, kind: str = "file") -> CanonicalResource:
    selected = selection.resolved_path
    incarnation = f"{selected.mount_id}:{selection.observed_generation}"
    path = selected.path if kind == "file" else ""
    identity = selection.backing_identity or incarnation
    return CanonicalResource(
        namespace="environment",
        kind=kind,
        identifier=f"{incarnation}:{path}" if kind == "file" else incarnation,
        approval_revision=f"{identity}:{path}",
    )
