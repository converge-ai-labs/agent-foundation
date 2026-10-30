---
title: Service settings reference
sidebarTitle: Settings reference
description: Every field of the Service settings, with its environment variable, type, bounds, and default.
---

Find a setting by its section, then use the row to choose its TOML key or environment variable and check the accepted type, bounds, and default. For a complete deployment configuration, start with [Configure Service](configuration.md); that guide covers precedence, role and storage requirements, and checks across fields.

For example, `server.port` is `port` in the TOML `[server]` section or `A13N_SERVER__PORT` in the environment. Rows marked **JSON field** share one environment variable: set `A13N_AUTH__MAIL` to a JSON object containing the mail settings. An environment value replaces that field's file value, including the whole object for nested settings.

These tables describe the built-in settings at this source version. Secret defaults are masked. Download [Service settings JSON](/reference/service-settings.json) for the full schema, including named enum and union definitions. Distribution-specific extension settings are documented by their distribution.

The tables are generated from `Settings`. Contributors updating those definitions regenerate the English reference with `uv run --locked python scripts/docs/references.py` and synchronize the translated rows.

## `server`

| Setting                    | Environment variable             | Type / choices  | Constraints and default                         |
| -------------------------- | -------------------------------- | --------------- | ----------------------------------------------- |
| `server.host`              | `A13N_SERVER__HOST`              | string          | default="127.0.0.1"                             |
| `server.port`              | `A13N_SERVER__PORT`              | integer         | minimum=1; maximum=65535; default=8000          |
| `server.public_url`        | `A13N_SERVER__PUBLIC_URL`        | string          | maxLength=2048; default="http://127.0.0.1:8000" |
| `server.control_url`       | `A13N_SERVER__CONTROL_URL`       | string or null  | maxLength=2048; default=null                    |
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
| `database.pool_size`                          | `A13N_DATABASE__POOL_SIZE`                          | integer        | minimum=1; maximum=1024; default=32               |
| `database.connect_timeout`                    | `A13N_DATABASE__CONNECT_TIMEOUT`                    | integer        | minimum=1; maximum=60; default=5                  |
| `database.statement_timeout`                  | `A13N_DATABASE__STATEMENT_TIMEOUT`                  | integer        | minimum=1; maximum=300; default=10                |
| `database.migration_advisory_lock_timeout`    | `A13N_DATABASE__MIGRATION_ADVISORY_LOCK_TIMEOUT`    | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_lock_timeout`             | `A13N_DATABASE__MIGRATION_LOCK_TIMEOUT`             | integer        | minimum=1; maximum=60; default=3                  |
| `database.migration_statement_timeout`        | `A13N_DATABASE__MIGRATION_STATEMENT_TIMEOUT`        | integer        | minimum=1; maximum=3600; default=900              |
| `database.migration_idle_transaction_timeout` | `A13N_DATABASE__MIGRATION_IDLE_TRANSACTION_TIMEOUT` | integer        | minimum=1; maximum=300; default=30                |

## `objects`

| Setting                         | Environment variable                  | Type / choices                    | Constraints and default                           |
| ------------------------------- | ------------------------------------- | --------------------------------- | ------------------------------------------------- |
| `objects.backend`               | `A13N_OBJECTS__BACKEND`               | "local", "s3"                     | default="local"                                   |
| `objects.root`                  | `A13N_OBJECTS__ROOT`                  | string                            | format="path"; default="var/service/objects"      |
| `objects.bucket`                | `A13N_OBJECTS__BUCKET`                | string or null                    | default=null                                      |
| `objects.prefix`                | `A13N_OBJECTS__PREFIX`                | string                            | default=""                                        |
| `objects.endpoint_url`          | `A13N_OBJECTS__ENDPOINT_URL`          | string or null                    | default=null                                      |
| `objects.path_style`            | `A13N_OBJECTS__PATH_STYLE`            | boolean                           | default=false                                     |
| `objects.addressing_style`      | `A13N_OBJECTS__ADDRESSING_STYLE`      | "auto", "path", "virtual" or null | default=null                                      |
| `objects.region`                | `A13N_OBJECTS__REGION`                | string or null                    | default=null                                      |
| `objects.access_key_id`         | `A13N_OBJECTS__ACCESS_KEY_ID`         | string or null                    | format="password"; default=null                   |
| `objects.secret_access_key`     | `A13N_OBJECTS__SECRET_ACCESS_KEY`     | string or null                    | format="password"; default=null                   |
| `objects.max_bytes`             | `A13N_OBJECTS__MAX_BYTES`             | integer                           | minimum=65536; maximum=67108864; default=16777216 |
| `objects.timeout`               | `A13N_OBJECTS__TIMEOUT`               | number                            | maximum=60; exclusiveMinimum=0; default=5         |
| `objects.upload_bytes`          | `A13N_OBJECTS__UPLOAD_BYTES`          | integer                           | minimum=1; maximum=33554432; default=1048576      |
| `objects.upload_limit`          | `A13N_OBJECTS__UPLOAD_LIMIT`          | integer                           | minimum=1; maximum=1000; default=60               |
| `objects.upload_window_seconds` | `A13N_OBJECTS__UPLOAD_WINDOW_SECONDS` | integer                           | minimum=1; maximum=3600; default=60               |

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
| `encryption.key_file`      | `A13N_ENCRYPTION__KEY_FILE`      | string or null | format="path"; default=null              |

## `control`

| Setting                               | Environment variable                        | Type / choices | Constraints and default                         |
| ------------------------------------- | ------------------------------------------- | -------------- | ----------------------------------------------- |
| `control.scan_seconds`                | `A13N_CONTROL__SCAN_SECONDS`                | number         | maximum=60; exclusiveMinimum=0; default=1       |
| `control.sweep_batch`                 | `A13N_CONTROL__SWEEP_BATCH`                 | integer        | minimum=1; maximum=10000; default=100           |
| `control.inbox_count`                 | `A13N_CONTROL__INBOX_COUNT`                 | integer        | minimum=1; maximum=10000; default=128           |
| `control.inbox_bytes`                 | `A13N_CONTROL__INBOX_BYTES`                 | integer        | minimum=1024; maximum=16777216; default=2097152 |
| `control.browser_control_min_seconds` | `A13N_CONTROL__BROWSER_CONTROL_MIN_SECONDS` | integer        | minimum=1; default=60                           |
| `control.browser_control_max_seconds` | `A13N_CONTROL__BROWSER_CONTROL_MAX_SECONDS` | integer        | minimum=1; default=3600                         |
| `control.browser_control_seconds`     | `A13N_CONTROL__BROWSER_CONTROL_SECONDS`     | integer        | minimum=1; default=300                          |
| `control.browser_notice_seconds`      | `A13N_CONTROL__BROWSER_NOTICE_SECONDS`      | integer        | minimum=60; maximum=604800; default=86400       |
| `control.subscriptions`               | `A13N_CONTROL__SUBSCRIPTIONS`               | integer        | minimum=1; maximum=1000; default=32             |
| `control.webhook_timeout`             | `A13N_CONTROL__WEBHOOK_TIMEOUT`             | number         | maximum=30; exclusiveMinimum=0; default=10      |
| `control.import_timeout`              | `A13N_CONTROL__IMPORT_TIMEOUT`              | number         | maximum=120; exclusiveMinimum=0; default=30     |
| `control.stream_refresh_seconds`      | `A13N_CONTROL__STREAM_REFRESH_SECONDS`      | number         | maximum=60; exclusiveMinimum=0; default=2       |

## `schedules`

| Setting                           | Environment variable                    | Type / choices | Constraints and default                    |
| --------------------------------- | --------------------------------------- | -------------- | ------------------------------------------ |
| `schedules.scan_seconds`          | `A13N_SCHEDULES__SCAN_SECONDS`          | number         | maximum=300; exclusiveMinimum=0; default=5 |
| `schedules.batch`                 | `A13N_SCHEDULES__BATCH`                 | integer        | minimum=1; maximum=1000; default=100       |
| `schedules.max_per_workspace`     | `A13N_SCHEDULES__MAX_PER_WORKSPACE`     | integer        | minimum=1; maximum=100000; default=1000    |
| `schedules.dispatch_max_attempts` | `A13N_SCHEDULES__DISPATCH_MAX_ATTEMPTS` | integer        | minimum=1; maximum=20; default=5           |

## `outbox`

| Setting                                       | Environment variable                                               | Type / choices | Constraints and default                        |
| --------------------------------------------- | ------------------------------------------------------------------ | -------------- | ---------------------------------------------- |
| `outbox.purge_interval_seconds`               | `A13N_OUTBOX__PURGE_INTERVAL_SECONDS`                              | number         | maximum=3600; exclusiveMinimum=0; default=60   |
| `outbox.purge_batch`                          | `A13N_OUTBOX__PURGE_BATCH`                                         | integer        | minimum=1; maximum=10000; default=1000         |
| `outbox.purge_budget_seconds`                 | `A13N_OUTBOX__PURGE_BUDGET_SECONDS`                                | number         | maximum=60; exclusiveMinimum=0; default=5      |
| `outbox.defaults.delivered_retention_seconds` | `A13N_OUTBOX__DEFAULTS` (JSON field `delivered_retention_seconds`) | integer        | minimum=1; maximum=31536000; default=86400     |
| `outbox.defaults.dead_retention_seconds`      | `A13N_OUTBOX__DEFAULTS` (JSON field `dead_retention_seconds`)      | integer        | minimum=1; maximum=31536000; default=1209600   |
| `outbox.defaults.max_attempts`                | `A13N_OUTBOX__DEFAULTS` (JSON field `max_attempts`)                | integer        | minimum=1; maximum=100; default=12             |
| `outbox.defaults.batch`                       | `A13N_OUTBOX__DEFAULTS` (JSON field `batch`)                       | integer        | minimum=1; maximum=1000; default=32            |
| `outbox.defaults.parallel`                    | `A13N_OUTBOX__DEFAULTS` (JSON field `parallel`)                    | integer        | minimum=1; maximum=128; default=8              |
| `outbox.defaults.lease_seconds`               | `A13N_OUTBOX__DEFAULTS` (JSON field `lease_seconds`)               | number         | minimum=10; maximum=600; default=60            |
| `outbox.defaults.backlog_count`               | `A13N_OUTBOX__DEFAULTS` (JSON field `backlog_count`)               | integer        | minimum=1; maximum=1000000; default=10000      |
| `outbox.defaults.backlog_age_seconds`         | `A13N_OUTBOX__DEFAULTS` (JSON field `backlog_age_seconds`)         | number         | maximum=86400; exclusiveMinimum=0; default=300 |
| `outbox.defaults.backlog_alert_seconds`       | `A13N_OUTBOX__DEFAULTS` (JSON field `backlog_alert_seconds`)       | number         | maximum=86400; exclusiveMinimum=0; default=300 |
| `outbox.by_kind`                              | `A13N_OUTBOX__BY_KIND`                                             | object         | —                                              |

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
| `worker.output_bytes`            | `A13N_WORKER__OUTPUT_BYTES`            | integer        | minimum=1024; maximum=16777216; default=1048576  |
| `worker.page_items`              | `A13N_WORKER__PAGE_ITEMS`              | integer        | minimum=16; maximum=4096; default=256            |
| `worker.page_bytes`              | `A13N_WORKER__PAGE_BYTES`              | integer        | minimum=65536; maximum=16777216; default=1048576 |
| `worker.content_bytes`           | `A13N_WORKER__CONTENT_BYTES`           | integer        | minimum=1024; maximum=16777216; default=65536    |
| `worker.compression_level`       | `A13N_WORKER__COMPRESSION_LEVEL`       | integer        | minimum=1; maximum=19; default=3                 |
| `worker.stream_length`           | `A13N_WORKER__STREAM_LENGTH`           | integer        | minimum=16; maximum=100000; default=10000        |
| `worker.stream_ttl`              | `A13N_WORKER__STREAM_TTL`              | integer        | minimum=1; maximum=86400; default=600            |
| `worker.stream_coalesce_seconds` | `A13N_WORKER__STREAM_COALESCE_SECONDS` | number         | minimum=0; maximum=1; default=0.1                |
| `worker.stream_trim_seconds`     | `A13N_WORKER__STREAM_TRIM_SECONDS`     | number         | minimum=0; maximum=600; default=10               |
| `worker.child_depth`             | `A13N_WORKER__CHILD_DEPTH`             | integer        | minimum=0; maximum=16; default=4                 |
| `worker.child_count`             | `A13N_WORKER__CHILD_COUNT`             | integer        | minimum=0; maximum=256; default=16               |

## `tool_executions`

| Setting                                       | Environment variable                                | Type / choices | Constraints and default                     |
| --------------------------------------------- | --------------------------------------------------- | -------------- | ------------------------------------------- |
| `tool_executions.max_active`                  | `A13N_TOOL_EXECUTIONS__MAX_ACTIVE`                  | integer        | minimum=1; maximum=1024; default=64         |
| `tool_executions.foreground_wait_seconds`     | `A13N_TOOL_EXECUTIONS__FOREGROUND_WAIT_SECONDS`     | number         | minimum=0; maximum=300; default=5           |
| `tool_executions.delivered_retention_seconds` | `A13N_TOOL_EXECUTIONS__DELIVERED_RETENTION_SECONDS` | integer        | minimum=1; maximum=31536000; default=3600   |
| `tool_executions.max_retention_seconds`       | `A13N_TOOL_EXECUTIONS__MAX_RETENTION_SECONDS`       | integer        | minimum=1; maximum=31536000; default=86400  |
| `tool_executions.shutdown_grace_seconds`      | `A13N_TOOL_EXECUTIONS__SHUTDOWN_GRACE_SECONDS`      | number         | minimum=0; maximum=3600; default=300        |
| `tool_executions.finalization_seconds`        | `A13N_TOOL_EXECUTIONS__FINALIZATION_SECONDS`        | number         | maximum=300; exclusiveMinimum=0; default=10 |

## `run_search`

| Setting                           | Environment variable                    | Type / choices | Constraints and default                        |
| --------------------------------- | --------------------------------------- | -------------- | ---------------------------------------------- |
| `run_search.user_head_bytes`      | `A13N_RUN_SEARCH__USER_HEAD_BYTES`      | integer        | minimum=256; maximum=6144; default=6144        |
| `run_search.user_tail_bytes`      | `A13N_RUN_SEARCH__USER_TAIL_BYTES`      | integer        | minimum=256; maximum=2048; default=2048        |
| `run_search.assistant_head_bytes` | `A13N_RUN_SEARCH__ASSISTANT_HEAD_BYTES` | integer        | minimum=256; maximum=4096; default=4096        |
| `run_search.assistant_tail_bytes` | `A13N_RUN_SEARCH__ASSISTANT_TAIL_BYTES` | integer        | minimum=256; maximum=12288; default=12288      |
| `run_search.vector_bytes`         | `A13N_RUN_SEARCH__VECTOR_BYTES`         | integer        | minimum=393216; maximum=786432; default=524288 |
| `run_search.target_bytes`         | `A13N_RUN_SEARCH__TARGET_BYTES`         | integer        | minimum=327680; maximum=655360; default=393216 |
| `run_search.messages`             | `A13N_RUN_SEARCH__MESSAGES`             | integer        | minimum=8; maximum=4096; default=512           |

## `environments`

| Setting                           | Environment variable                    | Type / choices  | Constraints and default                       |
| --------------------------------- | --------------------------------------- | --------------- | --------------------------------------------- |
| `environments.scan_seconds`       | `A13N_ENVIRONMENTS__SCAN_SECONDS`       | number          | maximum=300; exclusiveMinimum=0; default=5    |
| `environments.batch`              | `A13N_ENVIRONMENTS__BATCH`              | integer         | minimum=1; maximum=1000; default=16           |
| `environments.operation_seconds`  | `A13N_ENVIRONMENTS__OPERATION_SECONDS`  | number          | maximum=3600; exclusiveMinimum=0; default=120 |
| `environments.renewal_seconds`    | `A13N_ENVIRONMENTS__RENEWAL_SECONDS`    | number          | maximum=60; exclusiveMinimum=0; default=20    |
| `environments.wait_seconds`       | `A13N_ENVIRONMENTS__WAIT_SECONDS`       | number          | maximum=3600; exclusiveMinimum=0; default=300 |
| `environments.managed_count`      | `A13N_ENVIRONMENTS__MANAGED_COUNT`      | integer         | minimum=1; maximum=100000; default=100        |
| `environments.docker_host`        | `A13N_ENVIRONMENTS__DOCKER_HOST`        | string or null  | minLength=1; maxLength=2048; default=null     |
| `environments.docker_mount_roots` | `A13N_ENVIRONMENTS__DOCKER_MOUNT_ROOTS` | array of string | maxItems=64; default=[]                       |

## `provisioning`

| Setting                           | Environment variable                                   | Type / choices        | Constraints and default                   |
| --------------------------------- | ------------------------------------------------------ | --------------------- | ----------------------------------------- |
| `provisioning.local.enabled`      | `A13N_PROVISIONING__LOCAL` (JSON field `enabled`)      | boolean               | default=false                             |
| `provisioning.local.root`         | `A13N_PROVISIONING__LOCAL` (JSON field `root`)         | string or null        | format="path"; default=null               |
| `provisioning.docker.enabled`     | `A13N_PROVISIONING__DOCKER` (JSON field `enabled`)     | boolean               | default=false                             |
| `provisioning.docker.image`       | `A13N_PROVISIONING__DOCKER` (JSON field `image`)       | string or null        | minLength=1; maxLength=1024; default=null |
| `provisioning.docker.pull_policy` | `A13N_PROVISIONING__DOCKER` (JSON field `pull_policy`) | "never", "if_missing" | default="if_missing"                      |

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

| Setting                                   | Environment variable                            | Type / choices  | Constraints and default                                       |
| ----------------------------------------- | ----------------------------------------------- | --------------- | ------------------------------------------------------------- |
| `providers.forward_model_context_headers` | `A13N_PROVIDERS__FORWARD_MODEL_CONTEXT_HEADERS` | boolean         | default=true                                                  |
| `providers.forward_mcp_context_headers`   | `A13N_PROVIDERS__FORWARD_MCP_CONTEXT_HEADERS`   | boolean         | default=true                                                  |
| `providers.http_origins`                  | `A13N_PROVIDERS__HTTP_ORIGINS`                  | array of string | default=[]                                                    |
| `providers.require_https`                 | `A13N_PROVIDERS__REQUIRE_HTTPS`                 | boolean         | default=true                                                  |
| `providers.return_urls`                   | `A13N_PROVIDERS__RETURN_URLS`                   | array of string | default=[]                                                    |
| `providers.mcp_servers`                   | `A13N_PROVIDERS__MCP_SERVERS`                   | array of object | default=[]                                                    |
| `providers.flow_seconds`                  | `A13N_PROVIDERS__FLOW_SECONDS`                  | integer         | minimum=30; maximum=1800; default=600                         |
| `providers.operation_seconds`             | `A13N_PROVIDERS__OPERATION_SECONDS`             | number          | minimum=2; maximum=30; default=10                             |
| `providers.operation_scan_seconds`        | `A13N_PROVIDERS__OPERATION_SCAN_SECONDS`        | number          | maximum=3600; exclusiveMinimum=0; default=30                  |
| `providers.discovery_ttl`                 | `A13N_PROVIDERS__DISCOVERY_TTL`                 | integer         | minimum=1; maximum=86400; default=300                         |
| `providers.tool_call_seconds`             | `A13N_PROVIDERS__TOOL_CALL_SECONDS`             | number          | maximum=600; exclusiveMinimum=0; default=60                   |
| `providers.model_timeout`                 | `A13N_PROVIDERS__MODEL_TIMEOUT`                 | number          | maximum=3600; exclusiveMinimum=0; default=300                 |
| `providers.response_bytes`                | `A13N_PROVIDERS__RESPONSE_BYTES`                | integer         | minimum=65536; maximum=268435456; default=16777216            |
| `providers.chatgpt_client_id`             | `A13N_PROVIDERS__CHATGPT_CLIENT_ID`             | string or null  | minLength=1; maxLength=256; default=null                      |
| `providers.chatgpt_redirect_uri`          | `A13N_PROVIDERS__CHATGPT_REDIRECT_URI`          | string          | maxLength=2048; default="http://127.0.0.1:1456/auth/callback" |

## `plugins`

| Setting        | Environment variable | Type / choices  | Constraints and default |
| -------------- | -------------------- | --------------- | ----------------------- |
| `plugins.keys` | `A13N_PLUGINS__KEYS` | array of string | default=[]              |

## `composer`

| Setting           | Environment variable    | Type / choices  | Constraints and default                                                                |
| ----------------- | ----------------------- | --------------- | -------------------------------------------------------------------------------------- |
| `composer.models` | `A13N_COMPOSER__MODELS` | array of string | default=["gpt-5.6-luna", "claude-sonnet-5", "deepseek-v4.1-flash", "gemini-3.8-flash"] |

## `telemetry`

| Setting                         | Environment variable                  | Type / choices                      | Constraints and default                                         |
| ------------------------------- | ------------------------------------- | ----------------------------------- | --------------------------------------------------------------- |
| `telemetry.log_level`           | `A13N_TELEMETRY__LOG_LEVEL`           | "DEBUG", "INFO", "WARNING", "ERROR" | default="INFO"                                                  |
| `telemetry.log_format`          | `A13N_TELEMETRY__LOG_FORMAT`          | "pretty", "json"                    | default="json"                                                  |
| `telemetry.log_stdout`          | `A13N_TELEMETRY__LOG_STDOUT`          | boolean                             | default=true                                                    |
| `telemetry.log_file`            | `A13N_TELEMETRY__LOG_FILE`            | string or null                      | format="path"; default=null                                     |
| `telemetry.log_file_max_mb`     | `A13N_TELEMETRY__LOG_FILE_MAX_MB`     | integer                             | minimum=1; maximum=10240; default=100                           |
| `telemetry.log_file_backups`    | `A13N_TELEMETRY__LOG_FILE_BACKUPS`    | integer                             | minimum=1; maximum=100; default=5                               |
| `telemetry.metrics_port`        | `A13N_TELEMETRY__METRICS_PORT`        | integer or null                     | minimum=1; maximum=65535; default=null                          |
| `telemetry.trace_backend`       | `A13N_TELEMETRY__TRACE_BACKEND`       | "none", "langfuse", "logfire"       | default="none"                                                  |
| `telemetry.trace_url`           | `A13N_TELEMETRY__TRACE_URL`           | string or null                      | maxLength=2048; `pattern="^https?://[^\\s?#@]+$"`; default=null |
| `telemetry.langfuse_public_key` | `A13N_TELEMETRY__LANGFUSE_PUBLIC_KEY` | string or null                      | maxLength=256; default=null                                     |
| `telemetry.langfuse_secret_key` | `A13N_TELEMETRY__LANGFUSE_SECRET_KEY` | string or null                      | format="password"; default=null                                 |
| `telemetry.logfire_write_token` | `A13N_TELEMETRY__LOGFIRE_WRITE_TOKEN` | string or null                      | format="password"; default=null                                 |
| `telemetry.logfire_read_token`  | `A13N_TELEMETRY__LOGFIRE_READ_TOKEN`  | string or null                      | format="password"; default=null                                 |
| `telemetry.trace_content`       | `A13N_TELEMETRY__TRACE_CONTENT`       | "none", "standard", "full"          | default="standard"                                              |
| `telemetry.trace_query_timeout` | `A13N_TELEMETRY__TRACE_QUERY_TIMEOUT` | number                              | maximum=60; exclusiveMinimum=0; default=10                      |

## `monitoring`

| Setting                                    | Environment variable                             | Type / choices | Constraints and default                          |
| ------------------------------------------ | ------------------------------------------------ | -------------- | ------------------------------------------------ |
| `monitoring.enabled`                       | `A13N_MONITORING__ENABLED`                       | boolean        | default=true                                     |
| `monitoring.allow_loopback_ws`             | `A13N_MONITORING__ALLOW_LOOPBACK_WS`             | boolean        | default=false                                    |
| `monitoring.max_devices`                   | `A13N_MONITORING__MAX_DEVICES`                   | integer        | minimum=1; maximum=512; default=64               |
| `monitoring.poll_seconds`                  | `A13N_MONITORING__POLL_SECONDS`                  | number         | minimum=5; maximum=60; default=10                |
| `monitoring.request_seconds`               | `A13N_MONITORING__REQUEST_SECONDS`               | number         | minimum=1; maximum=30; default=5                 |
| `monitoring.query_concurrency`             | `A13N_MONITORING__QUERY_CONCURRENCY`             | integer        | minimum=1; maximum=64; default=8                 |
| `monitoring.query_seconds`                 | `A13N_MONITORING__QUERY_SECONDS`                 | number         | minimum=1; maximum=30; default=5                 |
| `monitoring.query_bytes`                   | `A13N_MONITORING__QUERY_BYTES`                   | integer        | minimum=65536; maximum=16777216; default=4194304 |
| `monitoring.deployment_preset`             | `A13N_MONITORING__DEPLOYMENT_PRESET`             | object or null | default=null                                     |
| `monitoring.deployment_query_bearer_token` | `A13N_MONITORING__DEPLOYMENT_QUERY_BEARER_TOKEN` | string or null | format="password"; default=null                  |
| `monitoring.collectors`                    | `A13N_MONITORING__COLLECTORS`                    | object         | —                                                |
| `monitoring.collector_seconds`             | `A13N_MONITORING__COLLECTOR_SECONDS`             | number         | minimum=10; maximum=300; default=30              |
| `monitoring.collector_targets`             | `A13N_MONITORING__COLLECTOR_TARGETS`             | integer        | minimum=1; maximum=4096; default=512             |
