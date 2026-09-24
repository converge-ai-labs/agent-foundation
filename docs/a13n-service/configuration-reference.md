# Service configuration reference

This field reference is generated from the same `Settings` definitions used by the Service loader. Run `uv run --locked python scripts/docs/references.py` after changing those definitions. Do not independently edit generated rows.

Use [Configure Service](configuration.md) for precedence, examples, role/storage requirements, and cross-field validation. Types and field constraints below do not replace those combined checks. Secret defaults are masked by the schema; this reference never reads deployment environment values. Defaults apply to the source version, not every historical release.

The complete machine-readable validation schema, including named enum/union definitions, is available as [Service settings JSON](../assets/reference/service-settings.json).

## `server`

| Setting                    | Environment variable             | Type / choices  | Constraints and default                         |
| -------------------------- | -------------------------------- | --------------- | ----------------------------------------------- |
| `server.host`              | `A13N_SERVER__HOST`              | string          | default="127.0.0.1"                             |
| `server.port`              | `A13N_SERVER__PORT`              | integer         | minimum=1; maximum=65535; default=8000          |
| `server.public_url`        | `A13N_SERVER__PUBLIC_URL`        | string          | maxLength=2048; default="http://127.0.0.1:8000" |
| `server.trusted_proxies`   | `A13N_SERVER__TRUSTED_PROXIES`   | array of string | default=[]                                      |
| `server.request_bytes`     | `A13N_SERVER__REQUEST_BYTES`     | integer         | minimum=1024; maximum=33554432; default=2097152 |
| `server.request_timeout`   | `A13N_SERVER__REQUEST_TIMEOUT`   | number          | maximum=60; exclusiveMinimum=0; default=10      |
| `server.readiness_timeout` | `A13N_SERVER__READINESS_TIMEOUT` | number          | maximum=30; exclusiveMinimum=0; default=2       |
| `server.shutdown_timeout`  | `A13N_SERVER__SHUTDOWN_TIMEOUT`  | integer         | minimum=1; maximum=300; default=15              |
| `server.tls_certificate`   | `A13N_SERVER__TLS_CERTIFICATE`   | string or null  | format="path"; default=null                     |
| `server.tls_key`           | `A13N_SERVER__TLS_KEY`           | string or null  | format="path"; default=null                     |

## `database`

| Setting                                       | Environment variable                                | Type / choices | Constraints and default                           |
| --------------------------------------------- | --------------------------------------------------- | -------------- | ------------------------------------------------- |
| `database.url`                                | `A13N_DATABASE__URL`                                | string         | format="password"; default="\*\*\*\*\*\*\*\*\*\*" |
| `database.auto_migrate`                       | `A13N_DATABASE__AUTO_MIGRATE`                       | boolean        | default=true                                      |
| `database.pool_size`                          | `A13N_DATABASE__POOL_SIZE`                          | integer        | minimum=1; maximum=100; default=5                 |
| `database.connect_timeout`                    | `A13N_DATABASE__CONNECT_TIMEOUT`                    | integer        | minimum=1; maximum=60; default=5                  |
| `database.statement_timeout`                  | `A13N_DATABASE__STATEMENT_TIMEOUT`                  | integer        | minimum=1; maximum=300; default=10                |
| `database.migration_advisory_lock_timeout`    | `A13N_DATABASE__MIGRATION_ADVISORY_LOCK_TIMEOUT`    | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_lock_timeout`             | `A13N_DATABASE__MIGRATION_LOCK_TIMEOUT`             | integer        | minimum=1; maximum=60; default=3                  |
| `database.migration_statement_timeout`        | `A13N_DATABASE__MIGRATION_STATEMENT_TIMEOUT`        | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_idle_transaction_timeout` | `A13N_DATABASE__MIGRATION_IDLE_TRANSACTION_TIMEOUT` | integer        | minimum=1; maximum=300; default=30                |

## `objects`

| Setting                         | Environment variable                  | Type / choices | Constraints and default                           |
| ------------------------------- | ------------------------------------- | -------------- | ------------------------------------------------- |
| `objects.backend`               | `A13N_OBJECTS__BACKEND`               | "local", "s3"  | default="local"                                   |
| `objects.root`                  | `A13N_OBJECTS__ROOT`                  | string         | format="path"; default="var/service/objects"      |
| `objects.bucket`                | `A13N_OBJECTS__BUCKET`                | string or null | default=null                                      |
| `objects.prefix`                | `A13N_OBJECTS__PREFIX`                | string         | default=""                                        |
| `objects.endpoint_url`          | `A13N_OBJECTS__ENDPOINT_URL`          | string or null | default=null                                      |
| `objects.path_style`            | `A13N_OBJECTS__PATH_STYLE`            | boolean        | default=false                                     |
| `objects.region`                | `A13N_OBJECTS__REGION`                | string or null | default=null                                      |
| `objects.access_key_id`         | `A13N_OBJECTS__ACCESS_KEY_ID`         | string or null | format="password"; default=null                   |
| `objects.secret_access_key`     | `A13N_OBJECTS__SECRET_ACCESS_KEY`     | string or null | format="password"; default=null                   |
| `objects.max_bytes`             | `A13N_OBJECTS__MAX_BYTES`             | integer        | minimum=65536; maximum=67108864; default=16777216 |
| `objects.timeout`               | `A13N_OBJECTS__TIMEOUT`               | number         | maximum=60; exclusiveMinimum=0; default=5         |
| `objects.upload_bytes`          | `A13N_OBJECTS__UPLOAD_BYTES`          | integer        | minimum=1; maximum=33554432; default=1048576      |
| `objects.upload_limit`          | `A13N_OBJECTS__UPLOAD_LIMIT`          | integer        | minimum=1; maximum=1000; default=60               |
| `objects.upload_window_seconds` | `A13N_OBJECTS__UPLOAD_WINDOW_SECONDS` | integer        | minimum=1; maximum=3600; default=60               |

## `redis`

| Setting         | Environment variable  | Type / choices | Constraints and default                           |
| --------------- | --------------------- | -------------- | ------------------------------------------------- |
| `redis.url`     | `A13N_REDIS__URL`     | string         | format="password"; default="\*\*\*\*\*\*\*\*\*\*" |
| `redis.timeout` | `A13N_REDIS__TIMEOUT` | number         | maximum=30; exclusiveMinimum=0; default=2         |

## `auth`

| Setting                     | Environment variable                           | Type / choices    | Constraints and default                       |
| --------------------------- | ---------------------------------------------- | ----------------- | --------------------------------------------- |
| `auth.session_seconds`      | `A13N_AUTH__SESSION_SECONDS`                   | integer           | minimum=60; maximum=604800; default=43200     |
| `auth.login_limit`          | `A13N_AUTH__LOGIN_LIMIT`                       | integer           | minimum=1; maximum=1000; default=10           |
| `auth.login_window_seconds` | `A13N_AUTH__LOGIN_WINDOW_SECONDS`              | integer           | minimum=1; maximum=3600; default=60           |
| `auth.invitation_seconds`   | `A13N_AUTH__INVITATION_SECONDS`                | integer           | minimum=3600; maximum=2592000; default=604800 |
| `auth.link_seconds`         | `A13N_AUTH__LINK_SECONDS`                      | integer           | minimum=300; maximum=86400; default=3600      |
| `auth.expiry_scan_seconds`  | `A13N_AUTH__EXPIRY_SCAN_SECONDS`               | number            | maximum=3600; exclusiveMinimum=0; default=60  |
| `auth.mail.smtp_host`       | `A13N_AUTH__MAIL` (JSON field `smtp_host`)     | string or null    | maxLength=253; default=null                   |
| `auth.mail.smtp_port`       | `A13N_AUTH__MAIL` (JSON field `smtp_port`)     | integer           | minimum=1; maximum=65535; default=587         |
| `auth.mail.smtp_security`   | `A13N_AUTH__MAIL` (JSON field `smtp_security`) | "starttls", "tls" | default="starttls"                            |
| `auth.mail.smtp_username`   | `A13N_AUTH__MAIL` (JSON field `smtp_username`) | string or null    | maxLength=320; default=null                   |
| `auth.mail.smtp_password`   | `A13N_AUTH__MAIL` (JSON field `smtp_password`) | string or null    | format="password"; default=null               |
| `auth.mail.sender`          | `A13N_AUTH__MAIL` (JSON field `sender`)        | string or null    | maxLength=320; default=null                   |
| `auth.mail.timeout`         | `A13N_AUTH__MAIL` (JSON field `timeout`)       | number            | maximum=60; exclusiveMinimum=0; default=10    |

## `encryption`

| Setting                    | Environment variable             | Type / choices | Constraints and default                  |
| -------------------------- | -------------------------------- | -------------- | ---------------------------------------- |
| `encryption.active_key_id` | `A13N_ENCRYPTION__ACTIVE_KEY_ID` | string or null | minLength=1; maxLength=128; default=null |
| `encryption.keys`          | `A13N_ENCRYPTION__KEYS`          | object         | —                                        |

## `control`

| Setting                          | Environment variable                   | Type / choices | Constraints and default                         |
| -------------------------------- | -------------------------------------- | -------------- | ----------------------------------------------- |
| `control.scan_seconds`           | `A13N_CONTROL__SCAN_SECONDS`           | number         | maximum=60; exclusiveMinimum=0; default=1       |
| `control.sweep_batch`            | `A13N_CONTROL__SWEEP_BATCH`            | integer        | minimum=1; maximum=10000; default=100           |
| `control.inbox_count`            | `A13N_CONTROL__INBOX_COUNT`            | integer        | minimum=1; maximum=10000; default=128           |
| `control.inbox_bytes`            | `A13N_CONTROL__INBOX_BYTES`            | integer        | minimum=1024; maximum=16777216; default=2097152 |
| `control.subscriptions`          | `A13N_CONTROL__SUBSCRIPTIONS`          | integer        | minimum=1; maximum=1000; default=32             |
| `control.outbox_batch`           | `A13N_CONTROL__OUTBOX_BATCH`           | integer        | minimum=1; maximum=1000; default=32             |
| `control.outbox_attempts`        | `A13N_CONTROL__OUTBOX_ATTEMPTS`        | integer        | minimum=1; maximum=100; default=12              |
| `control.outbox_lease_seconds`   | `A13N_CONTROL__OUTBOX_LEASE_SECONDS`   | integer        | minimum=10; maximum=600; default=60             |
| `control.outbox_retention_days`  | `A13N_CONTROL__OUTBOX_RETENTION_DAYS`  | integer        | minimum=1; maximum=365; default=14              |
| `control.webhook_timeout`        | `A13N_CONTROL__WEBHOOK_TIMEOUT`        | number         | maximum=30; exclusiveMinimum=0; default=10      |
| `control.import_timeout`         | `A13N_CONTROL__IMPORT_TIMEOUT`         | number         | maximum=120; exclusiveMinimum=0; default=30     |
| `control.stream_refresh_seconds` | `A13N_CONTROL__STREAM_REFRESH_SECONDS` | number         | maximum=60; exclusiveMinimum=0; default=2       |

## `worker`

| Setting                          | Environment variable                   | Type / choices | Constraints and default                          |
| -------------------------------- | -------------------------------------- | -------------- | ------------------------------------------------ |
| `worker.slots`                   | `A13N_WORKER__SLOTS`                   | integer        | minimum=1; maximum=128; default=4                |
| `worker.max_attempts`            | `A13N_WORKER__MAX_ATTEMPTS`            | integer        | minimum=1; maximum=20; default=3                 |
| `worker.lease_seconds`           | `A13N_WORKER__LEASE_SECONDS`           | integer        | minimum=3; maximum=300; default=30               |
| `worker.scan_seconds`            | `A13N_WORKER__SCAN_SECONDS`            | number         | maximum=30; exclusiveMinimum=0; default=1        |
| `worker.authority_seconds`       | `A13N_WORKER__AUTHORITY_SECONDS`       | number         | maximum=30; exclusiveMinimum=0; default=1        |
| `worker.drain_seconds`           | `A13N_WORKER__DRAIN_SECONDS`           | number         | maximum=300; exclusiveMinimum=0; default=10      |
| `worker.delivery_count`          | `A13N_WORKER__DELIVERY_COUNT`          | integer        | minimum=1; maximum=128; default=8                |
| `worker.delivery_bytes`          | `A13N_WORKER__DELIVERY_BYTES`          | integer        | minimum=1024; maximum=16777216; default=262144   |
| `worker.display_bytes`           | `A13N_WORKER__DISPLAY_BYTES`           | integer        | minimum=65536; maximum=67108864; default=8388608 |
| `worker.output_bytes`            | `A13N_WORKER__OUTPUT_BYTES`            | integer        | minimum=1024; maximum=16777216; default=1048576  |
| `worker.stream_length`           | `A13N_WORKER__STREAM_LENGTH`           | integer        | minimum=16; maximum=100000; default=10000        |
| `worker.stream_ttl`              | `A13N_WORKER__STREAM_TTL`              | integer        | minimum=1; maximum=86400; default=600            |
| `worker.stream_coalesce_seconds` | `A13N_WORKER__STREAM_COALESCE_SECONDS` | number         | minimum=0; maximum=1; default=0.1                |
| `worker.stream_trim_seconds`     | `A13N_WORKER__STREAM_TRIM_SECONDS`     | number         | minimum=0; maximum=600; default=10               |
| `worker.child_depth`             | `A13N_WORKER__CHILD_DEPTH`             | integer        | minimum=0; maximum=16; default=4                 |
| `worker.child_count`             | `A13N_WORKER__CHILD_COUNT`             | integer        | minimum=0; maximum=256; default=16               |

## `environments`

| Setting                           | Environment variable                    | Type / choices  | Constraints and default                       |
| --------------------------------- | --------------------------------------- | --------------- | --------------------------------------------- |
| `environments.scan_seconds`       | `A13N_ENVIRONMENTS__SCAN_SECONDS`       | number          | maximum=300; exclusiveMinimum=0; default=5    |
| `environments.batch`              | `A13N_ENVIRONMENTS__BATCH`              | integer         | minimum=1; maximum=1000; default=16           |
| `environments.operation_seconds`  | `A13N_ENVIRONMENTS__OPERATION_SECONDS`  | number          | maximum=3600; exclusiveMinimum=0; default=120 |
| `environments.renewal_seconds`    | `A13N_ENVIRONMENTS__RENEWAL_SECONDS`    | number          | maximum=60; exclusiveMinimum=0; default=20    |
| `environments.wait_seconds`       | `A13N_ENVIRONMENTS__WAIT_SECONDS`       | number          | maximum=3600; exclusiveMinimum=0; default=300 |
| `environments.managed_count`      | `A13N_ENVIRONMENTS__MANAGED_COUNT`      | integer         | minimum=1; maximum=100000; default=100        |
| `environments.allow_local`        | `A13N_ENVIRONMENTS__ALLOW_LOCAL`        | boolean         | default=false                                 |
| `environments.docker_host`        | `A13N_ENVIRONMENTS__DOCKER_HOST`        | string or null  | minLength=1; maxLength=2048; default=null     |
| `environments.docker_mount_roots` | `A13N_ENVIRONMENTS__DOCKER_MOUNT_ROOTS` | array of string | maxItems=64; default=[]                       |

## `memory`

| Setting                       | Environment variable                               | Type / choices | Constraints and default                             |
| ----------------------------- | -------------------------------------------------- | -------------- | --------------------------------------------------- |
| `memory.max_file_bytes`       | `A13N_MEMORY__MAX_FILE_BYTES`                      | integer        | minimum=1024; maximum=1048576; default=65536        |
| `memory.revisions_per_file`   | `A13N_MEMORY__REVISIONS_PER_FILE`                  | integer        | minimum=1; maximum=1000; default=10                 |
| `memory.max_total_bytes`      | `A13N_MEMORY__MAX_TOTAL_BYTES`                     | integer        | minimum=65536; maximum=1073741824; default=33554432 |
| `memory.mounts_per_thread`    | `A13N_MEMORY__MOUNTS_PER_THREAD`                   | integer        | minimum=1; maximum=32; default=8                    |
| `memory.guide_bytes`          | `A13N_MEMORY__GUIDE_BYTES`                         | integer        | minimum=256; maximum=65536; default=4096            |
| `memory.context_bytes`        | `A13N_MEMORY__CONTEXT_BYTES`                       | integer        | minimum=1024; maximum=1048576; default=32768        |
| `memory.always_load_bytes`    | `A13N_MEMORY__ALWAYS_LOAD_BYTES`                   | integer        | minimum=0; maximum=1048576; default=8192            |
| `memory.description_chars`    | `A13N_MEMORY__DESCRIPTION_CHARS`                   | integer        | minimum=1; maximum=1000; default=200                |
| `memory.frontmatter_bytes`    | `A13N_MEMORY__FRONTMATTER_BYTES`                   | integer        | minimum=128; maximum=16384; default=2048            |
| `memory.path_bytes`           | `A13N_MEMORY__PATH_BYTES`                          | integer        | minimum=16; maximum=1024; default=256               |
| `memory.write_retries`        | `A13N_MEMORY__WRITE_RETRIES`                       | integer        | minimum=0; maximum=10; default=3                    |
| `memory.recall_limit`         | `A13N_MEMORY__RECALL_LIMIT`                        | integer        | minimum=1; maximum=50; default=5                    |
| `memory.recall_bytes`         | `A13N_MEMORY__RECALL_BYTES`                        | integer        | minimum=512; maximum=65536; default=8192            |
| `memory.recall_seconds`       | `A13N_MEMORY__RECALL_SECONDS`                      | number         | maximum=30; exclusiveMinimum=0; default=2           |
| `memory.record_chars`         | `A13N_MEMORY__RECORD_CHARS`                        | integer        | minimum=1; maximum=8000; default=8000               |
| `memory.default_guide.file`   | `A13N_MEMORY__DEFAULT_GUIDE` (JSON field `file`)   | string or null | maxLength=65536; default=null                       |
| `memory.default_guide.record` | `A13N_MEMORY__DEFAULT_GUIDE` (JSON field `record`) | string or null | maxLength=65536; default=null                       |

## `providers`

| Setting                            | Environment variable                     | Type / choices  | Constraints and default                            |
| ---------------------------------- | ---------------------------------------- | --------------- | -------------------------------------------------- |
| `providers.private_domains`        | `A13N_PROVIDERS__PRIVATE_DOMAINS`        | array of string | default=[]                                         |
| `providers.private_cidrs`          | `A13N_PROVIDERS__PRIVATE_CIDRS`          | array of string | default=[]                                         |
| `providers.http_origins`           | `A13N_PROVIDERS__HTTP_ORIGINS`           | array of string | default=[]                                         |
| `providers.require_https`          | `A13N_PROVIDERS__REQUIRE_HTTPS`          | boolean         | default=true                                       |
| `providers.return_urls`            | `A13N_PROVIDERS__RETURN_URLS`            | array of string | default=[]                                         |
| `providers.mcp_servers`            | `A13N_PROVIDERS__MCP_SERVERS`            | McpServers      | default=[]                                         |
| `providers.flow_seconds`           | `A13N_PROVIDERS__FLOW_SECONDS`           | integer         | minimum=30; maximum=1800; default=600              |
| `providers.operation_seconds`      | `A13N_PROVIDERS__OPERATION_SECONDS`      | number          | minimum=2; maximum=30; default=10                  |
| `providers.operation_scan_seconds` | `A13N_PROVIDERS__OPERATION_SCAN_SECONDS` | number          | maximum=3600; exclusiveMinimum=0; default=30       |
| `providers.discovery_ttl`          | `A13N_PROVIDERS__DISCOVERY_TTL`          | integer         | minimum=1; maximum=86400; default=300              |
| `providers.tool_call_seconds`      | `A13N_PROVIDERS__TOOL_CALL_SECONDS`      | number          | maximum=600; exclusiveMinimum=0; default=60        |
| `providers.model_timeout`          | `A13N_PROVIDERS__MODEL_TIMEOUT`          | number          | maximum=3600; exclusiveMinimum=0; default=300      |
| `providers.response_bytes`         | `A13N_PROVIDERS__RESPONSE_BYTES`         | integer         | minimum=65536; maximum=268435456; default=16777216 |

## `plugins`

| Setting        | Environment variable | Type / choices  | Constraints and default |
| -------------- | -------------------- | --------------- | ----------------------- |
| `plugins.keys` | `A13N_PLUGINS__KEYS` | array of string | default=[]              |

## `assistant`

| Setting            | Environment variable     | Type / choices  | Constraints and default                                                                |
| ------------------ | ------------------------ | --------------- | -------------------------------------------------------------------------------------- |
| `assistant.models` | `A13N_ASSISTANT__MODELS` | array of string | default=["gpt-5.6-luna", "claude-sonnet-5", "deepseek-v4.1-flash", "gemini-3.8-flash"] |

## `telemetry`

| Setting                         | Environment variable                  | Type / choices                | Constraints and default                                         |
| ------------------------------- | ------------------------------------- | ----------------------------- | --------------------------------------------------------------- |
| `telemetry.log_format`          | `A13N_TELEMETRY__LOG_FORMAT`          | LogFormat                     | default="json"                                                  |
| `telemetry.trace_backend`       | `A13N_TELEMETRY__TRACE_BACKEND`       | "none", "langfuse", "logfire" | default="none"                                                  |
| `telemetry.trace_url`           | `A13N_TELEMETRY__TRACE_URL`           | string or null                | maxLength=2048; `pattern="^https?://[^\\s?#@]+$"`; default=null |
| `telemetry.langfuse_public_key` | `A13N_TELEMETRY__LANGFUSE_PUBLIC_KEY` | string or null                | maxLength=256; default=null                                     |
| `telemetry.langfuse_secret_key` | `A13N_TELEMETRY__LANGFUSE_SECRET_KEY` | string or null                | format="password"; default=null                                 |
| `telemetry.logfire_write_token` | `A13N_TELEMETRY__LOGFIRE_WRITE_TOKEN` | string or null                | format="password"; default=null                                 |
| `telemetry.logfire_read_token`  | `A13N_TELEMETRY__LOGFIRE_READ_TOKEN`  | string or null                | format="password"; default=null                                 |
| `telemetry.trace_content`       | `A13N_TELEMETRY__TRACE_CONTENT`       | "none", "standard", "full"    | default="standard"                                              |
| `telemetry.trace_query_timeout` | `A13N_TELEMETRY__TRACE_QUERY_TIMEOUT` | number                        | maximum=60; exclusiveMinimum=0; default=10                      |
