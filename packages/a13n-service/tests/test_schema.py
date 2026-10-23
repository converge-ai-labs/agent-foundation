"""Invariants every table of the composed schema keeps, checked on metadata rather than table by table."""

from a13n_service.distribution import OSS
from sqlalchemy import ForeignKeyConstraint, String

# Join tables are keyed by what they join, usage facts by the Harness's own record IDs, and a memory's store and
# revisions by the memory, provider authorization by its provider, provisioning facts by workspace/component, and a
# run's display pages by ordinal and pending answers by tool call. Desktop targets are keyed by native identity
# so aliases share the same authority. Desktop execution fences are keyed by Run. Memory organization state is keyed by Space;
# every other row has a Service object ID.
NOT_OBJECT_KEYED = {
    "desktop_targets",
    "desktop_executions",
    "memory_file_revisions",
    "memory_file_stores",
    "memory_record_sources",
    "model_provider_oauth",
    "passwords",
    "pending_answers",
    "run_item_pages",
    "memory_space_organizations",
    "thread_codeact",
    "thread_environments",
    "thread_memories",
    "usage_records",
    "workspace_provisioning",
}


def test_rows_have_object_ids_unless_keyed_by_another_owner() -> None:
    metadata = OSS.metadata()
    keyed = {
        name
        for name, table in metadata.tables.items()
        if [column.name for column in table.primary_key.columns] == ["id"]
        and isinstance(table.c.id.type, String)
        and table.c.id.type.length == 72
    }
    assert set(metadata.tables) - keyed == NOT_OBJECT_KEYED
    assert [column.name for column in metadata.tables["desktop_targets"].primary_key.columns] == ["key"]
    assert [column.name for column in metadata.tables["desktop_executions"].primary_key.columns] == ["run_id"]
    assert [column.name for column in metadata.tables["thread_codeact"].primary_key.columns] == ["thread_id"]
    assert [column.name for column in metadata.tables["memory_space_organizations"].primary_key.columns] == ["space_id"]
    assert [column.name for column in metadata.tables["memory_record_sources"].primary_key.columns] == [
        "memory_id",
        "record_id",
    ]
    assert [column.name for column in metadata.tables["pending_answers"].primary_key.columns] == [
        "run_id",
        "tool_call_id",
    ]


def test_workspace_owned_rows_reference_their_workspace_by_tenant_pair() -> None:
    """A row can never name a workspace of another organization: its workspace reference includes the organization."""
    missing = []
    for name, table in OSS.metadata().tables.items():
        if name == "workspaces" or not {"organization_id", "workspace_id"} <= set(table.c.keys()):
            continue
        pairs = {
            (tuple(fk.column_keys), tuple(element.target_fullname for element in fk.elements))
            for fk in table.constraints
            if isinstance(fk, ForeignKeyConstraint)
        }
        if (("organization_id", "workspace_id"), ("workspaces.organization_id", "workspaces.id")) not in pairs:
            missing.append(name)
    assert missing == []
