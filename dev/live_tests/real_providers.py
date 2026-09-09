"""Provision configured Providers through Control and clean owned remote targets."""

import logging
from contextlib import asynccontextmanager

import anyio

from .client import ACTIVE
from .management_support import ManagementJourney
from .round_two_lab import open_lab

logger = logging.getLogger(__name__)


async def provision_provider(journey, section, settings):
    if section == "environment":
        provider = await journey.post(
            journey.base + "/environment-providers",
            {
                "name": "Configured live Environment",
                "type": settings.type,
                "configuration": {},
                "credential": {"api_key": settings.api_key.get_secret_value()},
            },
        )
        template = await journey.post(
            journey.base + "/environment-templates",
            {
                "name": "Configured live sandbox",
                "provider_id": provider["id"],
                "access": "full",
                "preparation": "on_run",
                "retention": {"idle": {"stop_after": None, "delete_after": None}},
                "configuration": {"template": settings.template, "timeout_seconds": 300},
            },
        )
        return await journey.post(journey.base + "/environments", {"template_id": template["id"]})
    if section == "connector":
        return await journey.post(
            journey.base + "/connector-providers",
            {
                "name": "Configured live Connector",
                "type": settings.provider,
                "configuration": {"enabled_toolkits": settings.toolkits},
                "credentials": {"api_key": settings.api_key.get_secret_value()},
            },
        )
    if section != "model":
        raise ValueError("Unknown Provider section")
    provider = await journey.post(
        journey.base + "/model-providers",
        {
            "name": "Configured live Model",
            "type": settings.provider,
            "credential": settings.api_key.get_secret_value(),
            "configuration": {"base_url": settings.base_url, "auth_mode": "bearer"}
            if settings.provider == "openai_compatible"
            else {},
        },
    )
    return await journey.post(
        journey.base + "/models",
        {
            "key": "live-configured",
            "name": "Configured live Model",
            "provider_id": provider["id"],
            "upstream_model": settings.model,
            "model_api": "openrouter.chat_completions"
            if settings.provider == "openrouter"
            else "openai.chat_completions",
            "settings": {"timeout": 60, "max_tokens": 128},
        },
    )


async def cleanup_provider_lab(journey):
    """Only call for a fresh, exclusively owned lab; keep Worker alive for delete."""
    live = journey.live
    errors = []
    try:
        await live.cleanup()
        for run_id in live.runs:
            await live.wait(
                lambda run_id=run_id: live.run(run_id), lambda run: run["status"] not in ACTIVE, "Run cleanup"
            )
    except Exception as error:
        errors.append("Run cleanup: " + type(error).__name__)
    # Enumerate this new workspace so a lost create response cannot hide an owned target.
    for environment in await live.collection(journey.base + "/environments"):
        if environment["status"] == "deleted":
            continue
        try:
            result = await journey.environment_command(environment["id"], "delete")
            assert result["status"] == "deleted"
            logger.info("configured Environment deleted: %s", environment["id"])
        except Exception as error:
            errors.append(environment["id"] + ": " + type(error).__name__)
    assert not errors, "Provider cleanup failed: " + "; ".join(errors)


@asynccontextmanager
async def configured_provider_lab(section, settings):
    async with open_lab(suite="management") as lab:
        journey = ManagementJourney(lab)
        try:
            resource = await provision_provider(journey, section, settings)
            logger.info("configured %s persisted: %s", section, resource["id"])
            yield journey, resource
        finally:
            # Also run after partial provisioning, failed assertions and cancellation.
            # The outer lab removes the entire disposable DB, including encrypted keys.
            with anyio.CancelScope(shield=True), anyio.fail_after(180):
                await cleanup_provider_lab(journey)
