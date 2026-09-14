from a13n_service.iam.authorization import _WORKSPACE_ROLE_ACTIONS, WorkspaceAction


def test_workspace_roles_grant_connectivity_actions_explicitly() -> None:
    viewer = _WORKSPACE_ROLE_ACTIONS["viewer"]
    runner = _WORKSPACE_ROLE_ACTIONS["runner"]
    builder = _WORKSPACE_ROLE_ACTIONS["builder"]
    admin = _WORKSPACE_ROLE_ACTIONS["admin"]

    read_actions = {
        WorkspaceAction.application_account_read,
        WorkspaceAction.account_target_read,
        WorkspaceAction.connector_provider_read,
        WorkspaceAction.connection_read,
    }
    admin_only = {
        WorkspaceAction.application_account_manage,
        WorkspaceAction.connector_provider_manage,
    }

    assert read_actions <= viewer <= runner <= builder <= admin
    assert WorkspaceAction.connection_manage in builder
    assert WorkspaceAction.account_target_manage in builder
    assert WorkspaceAction.skill_revision_publish in builder
    assert admin_only.isdisjoint(builder)
    assert admin_only <= admin
