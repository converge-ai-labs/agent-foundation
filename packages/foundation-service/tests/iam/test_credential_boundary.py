from dataclasses import replace

import pytest
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.authorization import AuthorizationError


def test_organization_boundary_requires_exactly_one_scope_and_human_session():
    user = PrincipalRef(principal_type="user", principal_id="usr_1234567890abcdef")
    actor = AuthenticatedActor(
        principal=user, auth_method="session", credential_id="session", boundary_workspace_id="ws_test"
    )
    with pytest.raises(ValueError):
        replace(actor, boundary_organization_id="org_test")
    with pytest.raises(ValueError):
        replace(actor, boundary_workspace_id=None)
    organization = replace(actor, boundary_workspace_id=None, boundary_organization_id="org_test")
    with pytest.raises(AuthorizationError):
        _ = organization.workspace_id
    with pytest.raises(ValueError):
        replace(organization, auth_method="bearer")
    with pytest.raises(ValueError):
        replace(
            organization, principal=PrincipalRef(principal_type="service_account", principal_id="sa_1234567890abcdef")
        )
