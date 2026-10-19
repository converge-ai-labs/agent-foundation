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
            "list_agents_api_v1_agents_get",
            "validate_revision_api_v1_agents_validate_post",
            "get_agent_api_v1_agents__agent_id__get",
            "list_revisions_api_v1_agents__agent_id__revisions_get",
            "get_revision_api_v1_agents__agent_id__revisions__re_24e45e8cb6d5",
            "compare_revisions_api_v1_agents__agent_id__revision_3fb491060adb",
            "list_toolsets_api_v1_toolsets_get",
            "define_toolsets_api_v1_toolsets_definitions_get",
            "index_toolsets_api_v1_toolsets_index_get",
        ),
        "write": (
            "create_agent_api_v1_agents_post",
            "update_agent_api_v1_agents__agent_id__patch",
            "archive_agent_api_v1_agents__agent_id__archive_post",
            "duplicate_agent_api_v1_agents__agent_id__duplicate_post",
            "create_revision_api_v1_agents__agent_id__revisions_post",
            "set_default_api_v1_agents__agent_id__revisions__rev_fd48db7a69f3",
            "unarchive_agent_api_v1_agents__agent_id__unarchive_post",
        ),
    },
    "models": {
        "read": (
            "get_media_defaults_api_v1_media_understanding_defaults_get",
            "get_model_catalog_api_v1_model_catalog_get",
            "list_models_api_v1_models_get",
            "get_model_api_v1_models__key__get",
        ),
        "write": (
            "create_model_api_v1_models_post",
            "update_model_api_v1_models__key__patch",
        ),
    },
    "connections": {
        "read": (
            "list_connections_api_v1_connections_get",
            "get_connection_api_v1_connections__connection_id__get",
            "list_tools_api_v1_connections__connection_id__tools_get",
            "define_connection_tools_api_v1_connections__connect_926d72ff232f",
            "index_connection_tools_api_v1_connections__connecti_5bd15702e505",
            "list_apps_api_v1_connector_providers__provider_id__apps_get",
            "get_app_api_v1_connector_providers__provider_id__apps__app__get",
            "list_actions_api_v1_connector_providers__provider_i_d227e8b717f5",
            "define_connector_actions_api_v1_connector_providers_e99cf292e426",
            "index_connector_actions_api_v1_connector_providers__49f9dc3f7344",
            "list_mcp_servers_api_v1_mcp_servers_get",
        ),
        "write": (
            "create_connection_api_v1_connections_post",
            "update_connection_api_v1_connections__connection_id__patch",
            "revoke_connection_api_v1_connections__connection_id__revoke_post",
            "test_connection_api_v1_connections__connection_id__test_post",
        ),
    },
    "skills": {
        "read": (
            "list_skills_api_v1_skills_get",
            "validate_package_api_v1_skills_validate_post",
            "get_skill_api_v1_skills__skill_id__get",
            "list_revisions_api_v1_skills__skill_id__revisions_get",
            "get_revision_api_v1_skills__skill_id__revisions__re_b302d83353a3",
        ),
        "write": (
            "create_skill_api_v1_skills_post",
            "update_skill_api_v1_skills__skill_id__patch",
            "archive_skill_api_v1_skills__skill_id__archive_post",
            "create_revision_api_v1_skills__skill_id__revisions_post",
            "set_default_revision_api_v1_skills__skill_id__revis_54f51a99ce8b",
            "unarchive_skill_api_v1_skills__skill_id__unarchive_post",
        ),
    },
    "environment_templates": {
        "read": (
            "list_templates_api_v1_environment_templates_get",
            "get_template_api_v1_environment_templates__template_id__get",
        ),
        "write": (
            "create_template_api_v1_environment_templates_post",
            "update_template_api_v1_environment_templates__template_id__patch",
        ),
    },
    "providers": {
        "read": (
            "list_providers_api_v1_connector_providers_get",
            "get_provider_api_v1_connector_providers__provider_id__get",
            "list_providers_api_v1_environment_providers_get",
            "get_provider_api_v1_environment_providers__provider_id__get",
            "list_providers_api_v1_model_providers_get",
            "get_provider_api_v1_model_providers__provider_id__get",
            "discover_model_provider_models_api_v1_model_provide_aaf91efd6e23",
            "list_providers_api_v1_web_providers_get",
            "get_provider_api_v1_web_providers__provider_id__get",
        ),
        "write": (
            "test_provider_api_v1_connector_providers__provider_id__test_post",
            "test_provider_api_v1_environment_providers__provide_03e15087e087",
            "test_provider_api_v1_model_providers__provider_id__test_post",
            "test_provider_api_v1_web_providers__provider_id__test_post",
        ),
    },
    "execution": {
        "read": (
            "list_analyses_api_v1_finding_analyses_get",
            "list_findings_api_v1_findings_get",
            "get_finding_api_v1_findings__finding_id__get",
            "list_runs_api_v1_runs_get",
            "get_run_api_v1_runs__run_id__get",
            "run_agent_api_v1_runs__run_id__agent_get",
            "run_attempts_api_v1_runs__run_id__attempts_get",
            "get_attempt_api_v1_runs__run_id__attempts__attempt_id__get",
            "list_attempt_spans_api_v1_runs__run_id__attempts__a_e1f51e19dfb7",
            "run_content_api_v1_runs__run_id__contents__content_id__get",
            "run_items_api_v1_runs__run_id__items_get",
            "read_item_content_api_v1_runs__run_id__items__item__ca40c1d742b0",
            "run_lineage_api_v1_runs__run_id__lineage_get",
            "list_sessions_api_v1_sessions_get",
            "get_session_api_v1_sessions__session_id__get",
            "get_message_authors_api_v1_sessions__session_id__me_657b60807ed1",
            "list_threads_api_v1_threads_get",
            "get_thread_api_v1_threads__thread_id__get",
            "list_inbox_api_v1_threads__thread_id__inbox_get",
            "get_entry_api_v1_threads__thread_id__inbox__entry_id__get",
            "list_thread_runs_api_v1_threads__thread_id__runs_get",
            "get_trace_backend_api_v1_trace_backend_get",
            "list_traces_api_v1_traces_get",
            "get_trace_api_v1_traces__trace_id__get",
            "list_trace_spans_api_v1_traces__trace_id__spans_get",
            "read_span_content_api_v1_traces__trace_id__spans__s_22c99585bb45",
        ),
        "write": ("create_finding_api_v1_findings_post",),
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
