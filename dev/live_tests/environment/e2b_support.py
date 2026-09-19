"""Real E2B probes and cleanup restricted to identities allocated by one test."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import httpx
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.providers.environment.e2b.provider import E2BEnvironment, E2BProviderRuntime
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from e2b import AsyncSandbox
from e2b.exceptions import SandboxNotFoundException
from e2b.sandbox.sandbox_api import SandboxQuery

logger = logging.getLogger(__name__)
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=4096, max_output_bytes=65536, overflow="retain")


def command(script, **options):
    return CommandRequest(command=ShellCommand(profile_id="default", script=script), output_policy=OUTPUT, **options)


async def eventually(fetch, predicate, description, *, timeout=120):
    try:
        async with asyncio.timeout(timeout):
            while True:
                value = await fetch()
                if predicate(value):
                    return value
                await anyio.sleep(2)
    except TimeoutError:
        raise AssertionError(f"Timed out after {timeout}s: {description}") from None


async def past(deadline):
    """Wait against observed cloud expiry, never by shortening a mocked clock."""
    while (remaining := (deadline - datetime.now(UTC)).total_seconds()) > 0:
        await anyio.sleep(min(remaining, 1))


class E2BSandboxes:
    def __init__(self, settings):
        self.settings = settings
        self.configuration = E2BEnvironmentConfiguration(template=settings.template, timeout_seconds=300)
        self.identities = set()
        self.sandbox_ids = set()
        self.adapters = []

    @property
    def options(self):
        return {"api_key": self.settings.api_key.get_secret_value(), "domain": "e2b.dev", "request_timeout": 20}

    def adapter(self, *, state=None, identity=None, managed=True, configuration=None):
        identity = identity or "env-live-" + uuid4().hex
        self.identities.add(identity)
        if state is not None:
            self.sandbox_ids.add(state.state["sandbox_id"])
        adapter = E2BEnvironment(
            configuration or self.configuration,
            environment_id=identity,
            state=state,
            runtime=E2BProviderRuntime(self.settings.api_key, managed=managed, operation_id="op-live-" + uuid4().hex),
        )
        self.adapters.append(adapter)
        return adapter

    def fresh(self, original, *, state=True, managed=True):
        return self.adapter(
            state=original.dump_state() if state else None,
            identity=original.environment_id,
            managed=managed,
            configuration=original._configuration,
        )

    async def prepare(self, adapter=None):
        adapter = adapter or self.adapter()
        await adapter.enter(mount_id="m")
        try:
            await adapter.prepare()
        except Exception as error:
            logger.error(
                "E2B preparation failed: environment=%s code=%s cause=%s",
                adapter.environment_id,
                getattr(error, "code", type(error).__name__),
                type(error.__context__).__name__,
            )
            raise
        self.sandbox_ids.add(adapter.dump_state().state["sandbox_id"])
        return adapter

    async def targets(self, identity):
        assert identity in self.identities, "Only inspect identities owned by this test"
        pages = AsyncSandbox.list(
            query=SandboxQuery(metadata={"a13n_harness.providers.environment": identity}), limit=100, **self.options
        )
        result = []
        while pages.has_next:
            result.extend(await pages.next_items())
        self.sandbox_ids.update(item.sandbox_id for item in result)
        return result

    async def info(self, sandbox_id):
        assert sandbox_id in self.sandbox_ids, "Only inspect sandboxes owned by this test"
        for attempt in range(3):
            try:
                return await AsyncSandbox.get_info(sandbox_id, **self.options)
            except SandboxNotFoundException:
                return None
            except httpx.TransportError as error:
                if attempt == 2:
                    raise
                logger.warning("E2B read retry sandbox=%s error=%s", sandbox_id, type(error).__name__)
                await anyio.sleep(1)

    async def state(self, sandbox_id, expected):
        def matches(value):
            return ("absent" if value is None else value.state.value) == expected

        result = await eventually(lambda: self.info(sandbox_id), matches, f"E2B {sandbox_id}: {expected}")
        logger.info("E2B sandbox=%s state=%s expiry=%s", sandbox_id, expected, result.end_at if result else None)
        return result

    async def duplicate(self, original, *, compatible=True):
        self.identities.add(original.environment_id)
        sandbox = await AsyncSandbox.create(
            template=self.configuration.template,
            timeout=300,
            metadata={
                "a13n_harness.providers.environment": original.environment_id,
                "a13n_configuration": original._configuration.fingerprint if compatible else "incompatible",
            },
            secure=True,
            lifecycle={"on_timeout": "kill", "auto_resume": False},
            **self.options,
        )
        self.sandbox_ids.add(sandbox.sandbox_id)
        return sandbox

    async def cleanup(self):
        errors = []
        for adapter in self.adapters:
            state = adapter.dump_state()
            if state is not None:
                self.sandbox_ids.add(state.state["sandbox_id"])
            try:
                await adapter.close()
            except Exception as error:
                errors.append("close: " + type(error).__name__)
        # A create can succeed without returning state. Include paused targets too.
        for identity in self.identities:
            try:
                await self.targets(identity)
            except Exception as error:
                errors.append(identity + " discovery: " + type(error).__name__)
        for sandbox_id in sorted(self.sandbox_ids):
            try:
                await AsyncSandbox.kill(sandbox_id, **self.options)
                await self.state(sandbox_id, "absent")
            except Exception as error:
                errors.append(sandbox_id + " cleanup: " + type(error).__name__)
        assert not errors, "E2B cleanup failed: " + "; ".join(errors)
