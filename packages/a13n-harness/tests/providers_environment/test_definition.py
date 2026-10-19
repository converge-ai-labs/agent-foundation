"""Environment factories share one explicit per-execution input contract."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend_factory import BackendConnector
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL


@pytest.mark.anyio
@pytest.mark.parametrize("recover", [False, True])
async def test_factories_receive_recovery_keyword_even_when_none(tmp_path, recover):
    recovery = EnvdSessionRecovery() if recover else None
    factory = Mock(wraps=DIRECT_LOCAL.connector_factory)
    definition = replace(DIRECT_LOCAL, connector_factory=factory)
    connector = definition.execution_connector({"root": {"path": str(tmp_path)}}, session_recovery=recovery)
    assert factory.call_args.kwargs["session_recovery"] is recovery
    assert isinstance(connector, BackendConnector)
    execution_factory = Mock(wraps=connector.factory.execution)
    connector = replace(connector, factory=replace(connector.factory, execution=execution_factory))
    execution = await connector.open()
    try:
        assert execution_factory.call_args.kwargs["session_recovery"] is recovery
        assert execution.state is None
        if recovery is not None:
            assert recovery.state is None
    finally:
        await execution.close()
