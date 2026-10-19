"""remove legacy service toolsets

Revision ID: fed344f2d755
Revises: 4d51a94777d9

Removes the configuration, traces and findings toolsets, which platform tools replace, from stored agent
revisions and from the toolset overrides of run and inbox options. It also moves each platform tool selection
into the toolsets: a selected group becomes an enabled toolset whose tools keep the permission their effect had.
An override replaced the whole selection, so it also disables the groups it did not select. A rewritten revision
gets the digest of its new configuration. These rows are otherwise immutable, so each table's triggers are
disabled only around its update, inside the migration transaction; the rewrite changes no identity, state or
pointer they guard, and leaves no deferred check pending. The updates lock only rows that name a removed toolset
or a platform tool selection. Downgrade changes nothing; older builds cannot read platform toolsets.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "fed344f2d755"
down_revision = "4d51a94777d9"
branch_labels = None
depends_on = None

_REMOVED = ("configuration", "traces", "findings")
_NAMES_REMOVED = "'{configuration,traces,findings}'::text[]"
_DEFAULTS = {"read": "allow", "write": "ask"}
# Each platform group's tools at this revision, as MCP tool names by effect.
_PLATFORM: dict[str, dict[str, tuple[str, ...]]] = {
    "agents": {
        "read": (
            "list_agents",
            "validate_agent",
            "get_agent",
            "list_agent_revisions",
            "get_agent_revision",
            "compare_agent_revisions",
            "list_toolsets",
            "define_toolsets",
            "index_toolsets",
        ),
        "write": (
            "create_agent",
            "update_agent",
            "archive_agent",
            "duplicate_agent",
            "create_agent_revision",
            "set_default_agent_revision",
            "unarchive_agent",
        ),
    },
    "models": {
        "read": (
            "get_media_defaults",
            "get_model_catalog",
            "list_models",
            "get_model",
        ),
        "write": (
            "create_model",
            "update_model",
        ),
    },
    "connections": {
        "read": (
            "list_connections",
            "get_connection",
            "list_connection_tools",
            "define_connection_tools",
            "index_connection_tools",
            "list_connector_apps",
            "get_connector_app",
            "list_connector_actions",
            "define_connector_actions",
            "index_connector_actions",
            "list_mcp_servers",
        ),
        "write": (
            "create_connection",
            "update_connection",
            "revoke_connection",
            "test_connection",
        ),
    },
    "skills": {
        "read": (
            "list_skills",
            "validate_skill",
            "get_skill",
            "list_skill_revisions",
            "get_skill_revision",
        ),
        "write": (
            "create_skill",
            "update_skill",
            "archive_skill",
            "create_skill_revision",
            "set_default_skill_revision",
            "unarchive_skill",
        ),
    },
    "environment_templates": {
        "read": (
            "list_environment_templates",
            "get_environment_template",
        ),
        "write": (
            "create_environment_template",
            "update_environment_template",
        ),
    },
    "providers": {
        "read": (
            "list_connector_providers",
            "get_connector_provider",
            "list_environment_providers",
            "get_environment_provider",
            "list_model_providers",
            "get_model_provider",
            "discover_model_provider_models",
            "list_web_providers",
            "get_web_provider",
        ),
        "write": (
            "test_connector_provider",
            "test_environment_provider",
            "test_model_provider",
            "test_web_provider",
        ),
    },
    "execution": {
        "read": (
            "list_finding_analyses",
            "list_findings",
            "get_finding",
            "list_runs",
            "get_run",
            "get_run_agent",
            "list_run_attempts",
            "get_run_attempt",
            "list_attempt_spans",
            "get_run_content",
            "list_run_items",
            "read_run_item_content",
            "get_run_lineage",
            "list_sessions",
            "get_session",
            "get_message_authors",
            "list_threads",
            "get_thread",
            "list_inbox",
            "get_inbox_entry",
            "list_thread_runs",
            "get_trace_backend",
            "list_traces",
            "get_trace",
            "list_trace_spans",
            "read_span_content",
        ),
        "write": ("create_finding",),
    },
}


def _platform_toolsets(selection: dict, *, override: bool) -> dict[str, dict]:
    """Toolsets for a platform tool selection; only a non-default permission is stored, on each tool it covers."""
    permissions = {effect: selection.get(f"{effect}_permission", default) for effect, default in _DEFAULTS.items()}
    toolsets: dict[str, dict] = {}
    for group, effects in _PLATFORM.items():
        if group not in selection.get("groups", ()):
            if override:
                toolsets[group] = {"enabled": False}
            continue
        toolsets[group] = {
            "enabled": True,
            "tools": {
                key: {"permission": permissions[effect]}
                for effect, keys in effects.items()
                if permissions[effect] != _DEFAULTS[effect]
                for key in keys
            },
        }
    return toolsets


@contextmanager
def _unguarded(table: str) -> Iterator[None]:
    op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    yield
    op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def upgrade() -> None:
    bind = op.get_bind()
    rewrite = sa.text("UPDATE agent_revisions SET config = :config, digest = :digest WHERE id = :id").bindparams(
        sa.bindparam("config", type_=JSONB)
    )
    with _unguarded("agent_revisions"):
        stored = bind.execute(
            sa.text(
                "SELECT id, config FROM agent_revisions"
                f" WHERE config->'toolsets' ?| {_NAMES_REMOVED} OR config ? 'platform_tools'"
            )
        )
        for revision_id, config in stored.all():
            for key in _REMOVED:
                config["toolsets"].pop(key, None)
            config["toolsets"].update(_platform_toolsets(config.pop("platform_tools", {}), override=False))
            # The revision digest: SHA-256 of the canonical JSON it stores.
            digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            bind.execute(rewrite, {"id": revision_id, "config": config, "digest": digest})
    for table in ("runs", "inbox_entries"):
        rewrite = sa.text(f"UPDATE {table} SET options = :options WHERE id = :id").bindparams(
            sa.bindparam("options", type_=JSONB)
        )
        with _unguarded(table):
            # One update per row: a second update in this transaction would leave its foreign key checks pending.
            stored = bind.execute(
                sa.text(
                    f"SELECT id, options FROM {table} WHERE options #> '{{overrides,toolsets}}' ?| {_NAMES_REMOVED}"
                    " OR options->'overrides' ? 'platform_tools'"
                )
            )
            for row_id, options in stored.all():
                overrides = options["overrides"]
                selection = overrides.pop("platform_tools", None)
                toolsets = overrides.get("toolsets")
                if toolsets is not None or selection is not None:
                    overrides["toolsets"] = {
                        **{key: value for key, value in (toolsets or {}).items() if key not in _REMOVED},
                        **(_platform_toolsets(selection, override=True) if selection is not None else {}),
                    }
                bind.execute(rewrite, {"id": row_id, "options": options})


def downgrade() -> None:
    pass
