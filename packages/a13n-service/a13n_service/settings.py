"""Strict, once-per-process configuration: one section per concern, environment overrides a TOML file.

Every limit is finite and visible here. `A13N_<SECTION>__<FIELD>` overrides a field; unknown keys fail.
"""

import json
import os
import tomllib
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Any, Literal, get_args, get_origin
from urllib.parse import urlsplit

from a13n_logging import LogFile, LogFormat
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from a13n_service.infra.outbox import OutboxKind, Policy
from a13n_service.providers.tools.mcp_catalog import McpServers
from a13n_service.providers.traces import TraceProvider
from a13n_service.providers.traces.langfuse import Langfuse
from a13n_service.providers.traces.logfire import Logfire

ProcessRole = Literal["all", "control", "worker"]
LOOPBACK_NAMES = ("localhost", "127.0.0.1")


def _structured(annotation: Any) -> bool:
    """Whether a field takes a list, map or section rather than a scalar."""
    while True:
        annotation = getattr(annotation, "__value__", annotation)  # a `type` alias
        if get_origin(annotation) is not Annotated:
            break
        annotation = get_args(annotation)[0]
    origin = get_origin(annotation) or annotation
    return origin in (tuple, list, dict) or (isinstance(origin, type) and issubclass(origin, BaseModel))


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def decode_structured(cls, values: object) -> object:
        """Environment variables carry lists, maps and sections as JSON."""
        if not isinstance(values, dict):
            return values
        fields = cls.model_fields
        return {
            name: json.loads(value)
            if isinstance(value, str) and name in fields and _structured(fields[name].annotation)
            else value
            for name, value in values.items()
        }


class Server(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # The externally reachable URL: links, browser redirects such as OAuth callbacks, and the origin browser
    # requests may change state from. Its scheme decides whether cookies are `Secure`.
    public_url: str = Field(default="http://127.0.0.1:8000", max_length=2048)
    # Proxy addresses (IPs or CIDRs) whose X-Forwarded-For and X-Forwarded-Proto headers are trusted, so client
    # addresses, which rate limits key on, are the real clients' behind a reverse proxy.
    trusted_proxies: tuple[str, ...] = ()
    request_bytes: int = Field(default=2097152, ge=1024, le=33554432)
    request_timeout: float = Field(default=10, gt=0, le=60)
    readiness_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_timeout: int = Field(default=15, ge=1, le=300)
    tls_certificate: Path | None = None
    tls_key: Path | None = None

    @property
    def public_origin(self) -> str:
        """The origin of `public_url` as browsers send it in `Origin`: lowercase, default port omitted."""
        parts = urlsplit(self.public_url)
        default_port = {"http": ":80", "https": ":443"}.get(parts.scheme, "")
        return f"{parts.scheme}://{parts.netloc.lower().removesuffix(default_port)}"

    @property
    def public_origins(self) -> frozenset[str]:
        """The origins browsers reach the Service from: the public origin, and for a loopback `public_url` the same
        origin under the other loopback name, since `localhost` and `127.0.0.1` reach the same server."""
        parts = urlsplit(self.public_origin)
        if parts.hostname not in LOOPBACK_NAMES:
            return frozenset({self.public_origin})
        port = "" if parts.port is None else f":{parts.port}"
        return frozenset(f"{parts.scheme}://{name}{port}" for name in LOOPBACK_NAMES)

    @property
    def https(self) -> bool:
        """Whether browsers reach the Service over HTTPS, the only way its cookies can be `Secure`."""
        return urlsplit(self.public_url).scheme == "https"


class Database(Section):
    url: SecretStr = SecretStr("postgresql+psycopg://localhost/a13n_service")
    auto_migrate: bool = True
    pool_size: int = Field(default=5, ge=1, le=100)
    connect_timeout: int = Field(default=5, ge=1, le=60)
    statement_timeout: int = Field(default=10, ge=1, le=300)
    migration_advisory_lock_timeout: int = Field(default=900, ge=1, le=3600)
    migration_lock_timeout: int = Field(default=3, ge=1, le=60)
    migration_statement_timeout: int = Field(default=900, ge=1, le=3600)
    migration_idle_transaction_timeout: int = Field(default=30, ge=1, le=300)

    @field_validator("url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("database.url must use postgresql+psycopg")
        return value


class Objects(Section):
    backend: Literal["local", "s3"] = "local"
    root: Path = Path("var/service/objects")
    bucket: str | None = None
    prefix: str = ""
    endpoint_url: str | None = None
    # S3-compatible stores that address buckets by path rather than by host name, such as MinIO.
    path_style: bool = False
    addressing_style: Literal["auto", "path", "virtual"] | None = None
    region: str | None = None
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    max_bytes: int = Field(default=16777216, ge=65536, le=67108864)
    timeout: float = Field(default=5, gt=0, le=60)
    upload_bytes: int = Field(default=1048576, ge=1, le=33554432)
    upload_limit: int = Field(default=60, ge=1, le=1000)
    upload_window_seconds: int = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def s3_bucket(self) -> "Objects":
        if self.backend == "s3" and not self.bucket:
            raise ValueError("objects.bucket is required for the s3 backend")
        if self.path_style and self.addressing_style not in (None, "path"):
            raise ValueError("objects.path_style=true conflicts with objects.addressing_style other than path")
        return self

    @property
    def effective_addressing_style(self) -> Literal["auto", "path", "virtual"]:
        return self.addressing_style or ("path" if self.path_style else "auto")


class RedisSettings(Section):
    url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    timeout: float = Field(default=2, gt=0, le=30)


class Mail(Section):
    """SMTP delivery of identity mail. Without a host, invitation links are returned once to the inviter and
    password reset and email change are unavailable; no link is ever written to logs."""

    smtp_host: str | None = Field(default=None, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls"] = "starttls"
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: SecretStr | None = None
    sender: str | None = Field(default=None, max_length=320)
    timeout: float = Field(default=10, gt=0, le=60)

    @model_validator(mode="after")
    def complete(self) -> "Mail":
        delivery = (self.smtp_username, self.smtp_password, self.sender)
        if self.smtp_host is None and any(value is not None for value in delivery):
            raise ValueError("auth.mail credentials and sender require smtp_host")
        if self.smtp_host is not None and not self.sender:
            raise ValueError("auth.mail.sender is required with smtp_host")
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("auth.mail.smtp_username and smtp_password are configured together")
        return self


class Authentication(Section):
    session_seconds: int = Field(default=43200, ge=60, le=604800)
    login_limit: int = Field(default=10, ge=1, le=1000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    invitation_seconds: int = Field(default=604800, ge=3600, le=2592000)
    # One-use password-reset and email-change links.
    link_seconds: int = Field(default=3600, ge=300, le=86400)
    expiry_scan_seconds: float = Field(default=60, gt=0, le=3600)
    mail: Mail = Field(default_factory=Mail)


class Encryption(Section):
    active_key_id: str | None = Field(default=None, min_length=1, max_length=128)
    keys: dict[str, SecretStr] = Field(default_factory=dict)
    # Instead of the two above: one key read from this file, which the first start generates when it is missing.
    # Suits a single host whose data volume persists the file; back it up with the database.
    key_file: Path | None = None

    @model_validator(mode="after")
    def one_source(self) -> "Encryption":
        if self.key_file is not None and (self.active_key_id is not None or self.keys):
            raise ValueError("encryption.key_file excludes encryption.active_key_id and encryption.keys")
        return self

    @property
    def configured(self) -> bool:
        return self.active_key_id is not None or self.key_file is not None


class Control(Section):
    scan_seconds: float = Field(default=1, gt=0, le=60)
    sweep_batch: int = Field(default=100, ge=1, le=10000)
    inbox_count: int = Field(default=128, ge=1, le=10000)
    inbox_bytes: int = Field(default=2097152, ge=1024, le=16777216)
    # Per workspace; also bounds the subscriptions one transition stages deliveries for.
    subscriptions: int = Field(default=32, ge=1, le=1000)
    # One whole webhook POST.
    webhook_timeout: float = Field(default=10, gt=0, le=30)
    # Each request of a GitHub skill import, including the repository archive download.
    import_timeout: float = Field(default=30, gt=0, le=120)
    # How stale a thread stream's authority and thread snapshot may become while it is idle.
    stream_refresh_seconds: float = Field(default=2, gt=0, le=60)


class Outbox(Section):
    purge_interval_seconds: float = Field(default=60, gt=0, le=3600)
    purge_batch: int = Field(default=1000, ge=1, le=10000)
    purge_budget_seconds: float = Field(default=5, gt=0, le=60)
    defaults: Policy = Field(default_factory=Policy)
    # Only explicitly supplied fields override defaults, including when their value equals a built-in default.
    by_kind: dict[OutboxKind, Policy] = Field(default_factory=dict)

    @cached_property
    def policies(self) -> Mapping[OutboxKind, Policy]:
        policies = {}
        for kind in get_args(OutboxKind.__value__):
            override = self.by_kind[kind].model_dump(exclude_unset=True) if kind in self.by_kind else {}
            policies[kind] = Policy.model_validate(self.defaults.model_dump() | override)
        return MappingProxyType(policies)

    @model_validator(mode="after")
    def consistent(self) -> "Outbox":
        if self.purge_budget_seconds >= self.purge_interval_seconds:
            raise ValueError("outbox.purge_budget_seconds must stay below outbox.purge_interval_seconds")
        for kind, policy in self.policies.items():
            if policy.parallel > policy.batch:
                raise ValueError(f"outbox {kind}: parallel must not exceed batch")
        return self


class Worker(Section):
    slots: int = Field(default=4, ge=1, le=128)
    max_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    scan_seconds: float = Field(default=1, gt=0, le=30)
    authority_seconds: float = Field(default=1, gt=0, le=30)
    drain_seconds: float = Field(default=10, gt=0, le=300)
    # One boundary delivery batch of steers.
    delivery_count: int = Field(default=8, ge=1, le=128)
    delivery_bytes: int = Field(default=262144, ge=1024, le=16777216)
    display_bytes: int = Field(default=8388608, ge=65536, le=67108864)
    output_bytes: int = Field(default=1048576, ge=1024, le=16777216)
    # The thread stream's backstop cap. Coalesced text and reasoning append about ten entries a second, so one step
    # streams for about a quarter of an hour before the cap removes its start; boundaries trim covered entries first.
    stream_length: int = Field(default=10000, ge=16, le=100000)
    stream_ttl: int = Field(default=600, ge=1, le=86400)
    # Consecutive text, reasoning or tool-argument fragments within this window become one stream event; 0 streams
    # each fragment as it arrives.
    stream_coalesce_seconds: float = Field(default=0.1, ge=0, le=1)
    # How long entries a committed display covers stay in the stream, so a briefly disconnected reader resumes
    # without a gap; 0 removes them at the boundary.
    stream_trim_seconds: float = Field(default=10, ge=0, le=600)
    child_depth: int = Field(default=4, ge=0, le=16)
    child_count: int = Field(default=16, ge=0, le=256)


# A claim outlives its call deadline by this much, so its dispatcher can still publish what it observed.
PUBLISH_SECONDS = 10
# How long one renewal keeps a hosted sandbox at least: the Harness keepalive horizon, which the Service's E2B
# recipes cannot shorten. A renewal is due halfway to the expiry it reports.
RENEWAL_HORIZON_SECONDS = 300


class Environments(Section):
    # Maintenance and renewal interval, and the batch of each; idle thresholds are template policy.
    scan_seconds: float = Field(default=5, gt=0, le=300)
    batch: int = Field(default=16, ge=1, le=1000)
    # The bound of one claimed provider lifecycle call.
    operation_seconds: float = Field(default=120, gt=0, le=3600)
    # The bound of one renewal of a hosted sandbox, far shorter so that renewals come in time.
    renewal_seconds: float = Field(default=20, gt=0, le=60)
    # How long an attempt waits for its mounted instances to become ready.
    wait_seconds: float = Field(default=300, gt=0, le=3600)
    # Managed instances one workspace holds at most, counting every one not deleted; reservations beyond it,
    # explicit or by a run's agent template, are refused.
    managed_count: int = Field(default=100, ge=1, le=100000)
    # The Docker engine an account naming none uses; unset, the Service process's own Docker environment. Tenants
    # may name only a remote engine the outbound endpoint policy allows.
    docker_host: str | None = Field(default=None, min_length=1, max_length=2048)
    # Host directories a Docker recipe may bind below; empty refuses host mounts. A writable mount lets its
    # environment plant links that a later mount below the same root follows, so allow only directories whose
    # contents every workspace may share.
    docker_mount_roots: tuple[PurePosixPath, ...] = Field(default=(), max_length=64)

    @field_validator("docker_mount_roots")
    @classmethod
    def absolute_roots(cls, roots: tuple[PurePosixPath, ...]) -> tuple[PurePosixPath, ...]:
        if any(not root.is_absolute() or ".." in root.parts or "\x00" in str(root) for root in roots):
            raise ValueError("Docker mount roots must be absolute normalized paths")
        return roots


class LocalProvisioning(Section):
    # Local commands run under the Service account, without container isolation.
    enabled: bool = False
    root: Path | None = None

    @model_validator(mode="after")
    def explicit_root(self) -> "LocalProvisioning":
        if self.enabled and self.root is None:
            raise ValueError("provisioning.local.root is required when Local is enabled")
        if self.root is not None and (
            not self.root.is_absolute() or ".." in self.root.parts or "\x00" in str(self.root)
        ):
            raise ValueError("provisioning.local.root must be an absolute normalized path")
        return self


class DockerProvisioning(Section):
    enabled: bool = False
    image: str | None = Field(default=None, min_length=1, max_length=1024)
    pull_policy: Literal["never", "if_missing"] = "if_missing"


class Provisioning(Section):
    local: LocalProvisioning = Field(default_factory=LocalProvisioning)
    docker: DockerProvisioning = Field(default_factory=DockerProvisioning)


class DefaultGuides(Section):
    # Unset keeps the guide built into the Harness; a memory's own guide overrides either.
    file: str | None = Field(default=None, max_length=65536)
    record: str | None = Field(default=None, max_length=65536)


class MemorySettings(Section):
    """Memory limits. Lowering a file limit keeps existing content readable; the next write to a file enforces it."""

    max_file_bytes: int = Field(default=65536, ge=1024, le=1048576)
    # Revisions kept per file, pruned oldest first at the file's next write.
    revisions_per_file: int = Field(default=10, ge=1, le=1000)
    # Current content plus history of one memory; history is pruned oldest first before a write fails.
    max_total_bytes: int = Field(default=33554432, ge=65536, le=1073741824)
    mounts_per_thread: int = Field(default=8, ge=1, le=32)
    guide_bytes: int = Field(default=4096, ge=256, le=65536)
    # All memory context of one run: always-loaded files, indexes and changes.
    context_bytes: int = Field(default=32768, ge=1024, le=1048576)
    # The always-loaded content of one memory, within `context_bytes`.
    always_load_bytes: int = Field(default=8192, ge=0, le=1048576)
    description_chars: int = Field(default=200, ge=1, le=1000)
    frontmatter_bytes: int = Field(default=2048, ge=128, le=16384)
    path_bytes: int = Field(default=256, ge=16, le=1024)
    # Re-reads after a lost compare-and-swap before a tool call reports the conflict.
    write_retries: int = Field(default=3, ge=0, le=10)
    # Records one record mount recalls into a run's first input, and the bytes of its recall block.
    recall_limit: int = Field(default=5, ge=1, le=50)
    recall_bytes: int = Field(default=8192, ge=512, le=65536)
    # How long a run's recall waits; record mounts recall in parallel, and a late one is skipped.
    recall_seconds: float = Field(default=2, gt=0, le=30)
    # One record's text; mem0 accepts at most 8000 characters.
    record_chars: int = Field(default=8000, ge=1, le=8000)
    default_guide: DefaultGuides = Field(default_factory=DefaultGuides)

    @model_validator(mode="after")
    def consistent(self) -> "MemorySettings":
        if self.always_load_bytes > self.context_bytes:
            raise ValueError("memory.always_load_bytes must not exceed memory.context_bytes")
        if self.frontmatter_bytes >= self.max_file_bytes:
            raise ValueError("memory.frontmatter_bytes must stay below memory.max_file_bytes")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("memory.max_file_bytes must not exceed memory.max_total_bytes")
        for kind, guide in (("file", self.default_guide.file), ("record", self.default_guide.record)):
            if guide is not None and len(guide.encode()) > self.guide_bytes:
                raise ValueError(f"memory.default_guide.{kind} must fit memory.guide_bytes")
        return self


class Plugins(Section):
    # Installed Harness plugin factories agents may select, by entry-point key; nothing else is imported.
    keys: tuple[str, ...] = ()


class Composer(Section):
    # Upstream model names, most preferred first, that Agent Composer runs on when the workspace has
    # them; otherwise it runs on the workspace's first usable model by key. A `vendor/` prefix is ignored.
    models: tuple[str, ...] = ("gpt-5.6-luna", "claude-sonnet-5", "deepseek-v4.1-flash", "gemini-3.8-flash")


class Providers(Section):
    """Outbound network policy, call bounds and browser authorization flows for every provider and connection."""

    private_domains: tuple[str, ...] = ()
    private_cidrs: tuple[str, ...] = ()
    http_origins: tuple[str, ...] = ()
    require_https: bool = True
    # Exact URLs a browser authorization may return to after the connection callback, besides any page on the
    # origin of `server.public_url`, where the Console is served.
    return_urls: tuple[str, ...] = ()
    # Remote MCP servers suggested besides the packaged ones; an entry replaces the packaged one with its key.
    mcp_servers: McpServers = ()
    flow_seconds: int = Field(default=600, ge=30, le=1800)
    # Bounded provider operations, such as authorization steps and resource tests.
    operation_seconds: float = Field(default=10, ge=2, le=30)
    # How often connection operations whose owner vanished past their deadline are recovered.
    operation_scan_seconds: float = Field(default=30, gt=0, le=3600)
    discovery_ttl: int = Field(default=300, ge=1, le=86400)
    # One tool call to a connection, and each request it makes.
    tool_call_seconds: float = Field(default=60, gt=0, le=600)
    # Per-read timeout of one model exchange; reasoning models can stay silent for minutes.
    model_timeout: float = Field(default=300, gt=0, le=3600)
    response_bytes: int = Field(default=16777216, ge=65536, le=268435456)


class Telemetry(Section):
    """Logs, metrics, and the one trace backend Harness spans are exported to and trace queries read."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # The format of stdout; the log file is always JSON.
    log_format: LogFormat = LogFormat.json
    log_stdout: bool = True
    # A size-rotated log file owned by this process; processes must not share one path.
    log_file: Path | None = None
    log_file_max_mb: int = Field(default=100, ge=1, le=10240)
    # Rotated files kept besides the active one; rotation needs at least one.
    log_file_backups: int = Field(default=5, ge=1, le=100)
    # Serves Prometheus metrics at /metrics on `server.host` and this port; unset turns metrics off.
    metrics_port: int | None = Field(default=None, ge=1, le=65535)
    trace_backend: Literal["none", "langfuse", "logfire"] = "none"
    # The backend's API origin, such as https://cloud.langfuse.com or https://logfire-us.pydantic.dev.
    trace_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s?#@]+$")
    langfuse_public_key: str | None = Field(default=None, max_length=256)
    langfuse_secret_key: SecretStr | None = None
    logfire_write_token: SecretStr | None = None
    logfire_read_token: SecretStr | None = None
    # Whether prompts, outputs and tool payloads leave the deployment with the spans.
    trace_content: Literal["none", "standard", "full"] = "standard"
    trace_query_timeout: float = Field(default=10, gt=0, le=60)

    @model_validator(mode="after")
    def complete(self) -> "Telemetry":
        if not self.log_stdout and self.log_file is None:
            raise ValueError("telemetry.log_stdout = false requires telemetry.log_file")
        self.trace_config()
        return self

    def log_output(self) -> LogFile | None:
        """The rotating log file, when configured."""
        if self.log_file is None:
            return None
        return LogFile(path=self.log_file, max_bytes=self.log_file_max_mb * 1024 * 1024, backups=self.log_file_backups)

    def trace_config(self) -> TraceProvider | None:
        """The selected backend, for export and query; None when disabled. Each backend checks its own keys."""
        url = self.trace_url.rstrip("/") if self.trace_url else None
        timeout = self.trace_query_timeout
        match self.trace_backend:
            case "none":
                return None
            case "langfuse":
                return Langfuse.configure(url, self.langfuse_public_key, self.langfuse_secret_key, timeout=timeout)
            case "logfire":
                return Logfire.configure(url, self.logfire_write_token, self.logfire_read_token, timeout=timeout)


class Settings(Section):
    server: Server = Field(default_factory=Server)
    database: Database = Field(default_factory=Database)
    objects: Objects = Field(default_factory=Objects)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    auth: Authentication = Field(default_factory=Authentication)
    encryption: Encryption = Field(default_factory=Encryption)
    control: Control = Field(default_factory=Control)
    outbox: Outbox = Field(default_factory=Outbox)
    worker: Worker = Field(default_factory=Worker)
    environments: Environments = Field(default_factory=Environments)
    provisioning: Provisioning = Field(default_factory=Provisioning)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    providers: Providers = Field(default_factory=Providers)
    plugins: Plugins = Field(default_factory=Plugins)
    composer: Composer = Field(default_factory=Composer)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    # Sections a distribution declares, validated by their own types.
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metrics_port_is_free(self) -> "Settings":
        if self.telemetry.metrics_port == self.server.port:
            raise ValueError("telemetry.metrics_port must differ from server.port")
        return self

    @model_validator(mode="after")
    def mail_is_encrypted(self) -> "Settings":
        if self.auth.mail.smtp_host is not None and not self.encryption.configured:
            raise ValueError("auth.mail requires an encryption key: queued mail carries encrypted links")
        return self

    @model_validator(mode="after")
    def bounds_nest(self) -> "Settings":
        """Each bound must fit inside the one that contains it, or valid-looking values break every call."""
        worker, control = self.worker, self.control
        nested = (
            # A blocking Redis read must return before the client's socket timeout cuts it off.
            ("worker.scan_seconds", worker.scan_seconds, "redis.timeout", self.redis.timeout),
            ("thread stream block (1 s)", 1, "redis.timeout", self.redis.timeout),
            # An attempt survives one failed renewal, and still has room to write an object before it expires.
            ("worker.authority_seconds", 3 * worker.authority_seconds, "worker.lease_seconds", worker.lease_seconds),
            ("objects.timeout", 3 * self.objects.timeout, "worker.lease_seconds", worker.lease_seconds),
            # A sender finishes and settles within its outbox claim.
            (
                "control.webhook_timeout",
                2 * control.webhook_timeout,
                "outbox webhook lease_seconds",
                self.outbox.policies["webhook"].lease_seconds,
            ),
            (
                "auth.mail.timeout",
                2 * self.auth.mail.timeout,
                "outbox email lease_seconds",
                self.outbox.policies["email"].lease_seconds,
            ),
            # A memory namespace purge is one bounded provider operation.
            (
                "providers.operation_seconds",
                2 * self.providers.operation_seconds,
                "outbox memory_purge lease_seconds",
                self.outbox.policies["memory_purge"].lease_seconds,
            ),
            (
                "objects.timeout",
                2 * self.objects.timeout,
                "outbox checkpoint_cleanup lease_seconds",
                self.outbox.policies["checkpoint_cleanup"].lease_seconds,
            ),
            # Draining workers hand off before shutdown stops waiting for them.
            ("worker.drain_seconds", worker.drain_seconds, "server.shutdown_timeout", self.server.shutdown_timeout),
            # An upload is one request body.
            ("objects.upload_bytes", self.objects.upload_bytes, "server.request_bytes", self.server.request_bytes),
            # A child result, its output plus the envelope naming it, always fits the parent's empty inbox.
            ("worker.output_bytes", worker.output_bytes + 65536, "control.inbox_bytes", control.inbox_bytes),
            # A renewal due halfway to a hosted sandbox's expiry finishes before it: the pass that just missed it may
            # still be renewing others, then the next waits one interval, calls and publishes.
            (
                "environments.scan_seconds + 2 * environments.renewal_seconds",
                self.environments.scan_seconds + 2 * self.environments.renewal_seconds + PUBLISH_SECONDS,
                "half the renewal horizon",
                RENEWAL_HORIZON_SECONDS / 2,
            ),
        )
        for inner, inner_value, outer, outer_value in nested:
            if inner_value >= outer_value:
                raise ValueError(f"{inner} (with its margin: {inner_value}) must stay below {outer} ({outer_value})")
        return self


def load_settings(path: Path | None = None, *, extensions: Mapping[str, type[Section]] = {}) -> Settings:
    selected = path or (Path(os.environ["A13N_SETTINGS_FILE"]) if "A13N_SETTINGS_FILE" in os.environ else None)
    values: dict[str, Any] = tomllib.loads(selected.read_text()) if selected else {}
    known = set(Settings.model_fields) - {"extensions"}
    if shadowed := known & set(extensions):
        raise ValueError(f"Distribution settings sections shadow core sections: {sorted(shadowed)}")
    for name, value in os.environ.items():
        if not name.startswith("A13N_") or name == "A13N_SETTINGS_FILE":
            continue
        parts = name.removeprefix("A13N_").lower().split("__")
        if len(parts) != 2 or parts[0] not in known | set(extensions):
            raise ValueError(f"Unknown Service setting: {name}")
        section = values.setdefault(parts[0], {})
        if not isinstance(section, dict):
            raise ValueError(f"Invalid configuration section: {parts[0]}")
        section[parts[1]] = value
    unknown = set(values) - known - set(extensions)
    if unknown:
        raise ValueError(f"Unknown Service settings sections: {sorted(unknown)}")
    declared = {name: kind.model_validate(values.pop(name, {})) for name, kind in extensions.items()}
    return Settings.model_validate({**values, "extensions": declared})
