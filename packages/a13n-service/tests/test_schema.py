"""Invariants every table of the composed schema keeps, checked on metadata rather than table by table."""

from a13n_service.distribution import OSS
from sqlalchemy import ForeignKeyConstraint, String

# Join tables are keyed by what they join, usage facts by the Harness's own record IDs, and a memory's store and
# revisions by the memory, and provisioning facts by workspace/component; every other row has a Service object ID.
NOT_OBJECT_KEYED = {
    "memory_file_revisions",
    "memory_file_stores",
    "passwords",
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
