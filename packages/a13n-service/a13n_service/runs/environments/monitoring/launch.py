"""Freeze sampler and exporter inputs with the first recipe, before external creation."""

import json
from urllib.parse import quote

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.errors import disabled
from a13n_service.providers.environment.metrics import MetricsLaunch
from a13n_service.resources.metrics_data_sources.schemas import Configuration, Export
from a13n_service.resources.metrics_data_sources.service import headers
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments.schemas import MetricsApplication
from a13n_service.runs.environments.tables import EnvironmentRow


def location(environment: EnvironmentRow) -> SecretLocation:
    return SecretLocation(environment.organization_id, environment.__tablename__, "metrics_launch", environment.id)


def exporter_environment(export: Export, credentials: dict[str, str], environment: EnvironmentRow) -> dict[str, str]:
    """Metrics-specific OTEL settings; read credentials never cross this boundary."""
    return {
        "OTEL_SDK_DISABLED": "false",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": export.endpoint,
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": export.protocol,
        # An unauthenticated selection must not inherit an image's generic headers.
        "OTEL_EXPORTER_OTLP_HEADERS": "",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": ",".join(
            f"{name}={quote(value, safe='')}" for name, value in sorted(credentials.items())
        ),
        "OTEL_METRIC_EXPORT_INTERVAL": str(export.interval_seconds * 1000),
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"environment_id={environment.id},workspace_id={environment.workspace_id},"
            f"organization_id={environment.organization_id}"
        ),
    }


async def freeze(session: AsyncSession, keys: KeyRing, environment: EnvironmentRow, *, supported: bool) -> None:
    """Called only before the ordinary handle is first frozen. No provider I/O."""
    if environment.metrics_data_source_id is None:
        return
    source = await session.get_one(DataSourceRow, environment.metrics_data_source_id)
    config = Configuration.model_validate(source.config)
    if config.collection == "external":
        return
    if not source.enabled:
        raise disabled(source.KIND, source.id)
    if not supported:
        if config.collection != "native":
            return
        raise provider_error(
            "environment",
            "native_metrics_unsupported",
            EnvironmentProviderErrorCategory.UNSUPPORTED,
            description="Native metrics require a managed Docker or E2B Envd HTTP launch",
        )
    values = {}
    if config.collection == "native":
        assert config.export is not None
        values = exporter_environment(config.export, headers(keys, source, "export"), environment)
    launch = MetricsLaunch(environment=values)
    environment.metrics_launch = keys.protect(
        json.dumps({"environment": launch.environment, "sample_interval_ms": launch.sample_interval_ms}).encode(),
        location(environment),
    ).model_dump()
    environment.metrics_application = MetricsApplication(
        source_id=source.id,
        source_version=source.version,
        collection=config.collection,
        sample_interval_ms=launch.sample_interval_ms,
    ).model_dump(mode="json")


def restore(keys: KeyRing, environment: EnvironmentRow) -> MetricsLaunch | None:
    if environment.metrics_launch is None:
        return None
    return MetricsLaunch(
        **json.loads(keys.reveal(Envelope.model_validate(environment.metrics_launch), location(environment)))
    )
