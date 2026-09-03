from a13n_service.iam.authorization import _WORKSPACE_ROLE_ACTIONS, WorkspaceAction


def test_workspace_roles_grant_connectivity_actions_explicitly() -> None:
    viewer = _WORKSPACE_ROLE_ACTIONS["viewer"]
    runner = _WORKSPACE_ROLE_ACTIONS["runner"]
    builder = _WORKSPACE_ROLE_ACTIONS["builder"]
    admin = _WORKSPACE_ROLE_ACTIONS["admin"]

    read_actions = {
        WorkspaceAction.ingress_read,
        WorkspaceAction.route_read,
        WorkspaceAction.connector_read,
        WorkspaceAction.connector_connection_read,
        WorkspaceAction.mcp_connection_read,
    }
    admin_only = {
        WorkspaceAction.ingress_manage,
        WorkspaceAction.connector_manage,
        WorkspaceAction.connector_connection_manage,
        WorkspaceAction.mcp_connection_manage,
    }

    assert read_actions <= viewer <= runner <= builder <= admin
    assert WorkspaceAction.route_manage in builder
    assert admin_only.isdisjoint(builder)
    assert admin_only <= admin
    assert WorkspaceAction.plugin_manage not in admin
    assert WorkspaceAction.plugin_runtime_manage not in admin
