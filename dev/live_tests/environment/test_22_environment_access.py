"""Case 22: file/Shell exposure, actual effects and denied writes by access level."""

import pytest

from .service_cases import assert_access_policy

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("access", ["none", "read_only", "read_write", "full"])
async def test_environment_access_controls_tools_and_actual_files(management, access):
    environment, root = await management.environment(access="full" if access == "none" else access)
    await assert_access_policy(management, environment, access, root=root)
