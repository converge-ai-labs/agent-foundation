# Service configuration reference

This field reference is generated from the same `Settings` and `configuration_fields()` definitions used by the Service loader. Run `uv run --locked python scripts/docs/references.py` after changing those definitions. Do not independently edit generated rows.

Use [Configure Service](configuration.md) for precedence, examples, role/storage requirements, and cross-field validation. Types and field constraints below do not replace those combined checks. Secret defaults are masked by the schema; this reference never reads deployment environment values. Defaults apply to the source version, not every historical release.

The complete machine-readable validation schema, including named enum/union definitions, is available as [Service settings JSON](../assets/reference/service-settings.json).

## `service`

| Setting                               | Environment variable                       | Type / choices                             | Constraints and default                       |
| ------------------------------------- | ------------------------------------------ | ------------------------------------------ | --------------------------------------------- |
| `service.name`                        | `A13N_SERVICE_SERVICE_NAME`                | string                                     | default="a13n-service"                        |
| `service.role`                        | `A13N_SERVICE_ROLE`                        | "all", "control", "worker", "connectivity" | default="all"                                 |
| `service.host`                        | `A13N_SERVICE_HOST`                        | string                                     | default="127.0.0.1"                           |
| `service.port`                        | `A13N_SERVICE_PORT`                        | integer                                    | minimum=1; maximum=65535; default=8000        |
| `service.build_version`               | `A13N_SERVICE_BUILD_VERSION`               | string                                     | default="0.0.0"                               |
| `service.deployment_environment_name` | `A13N_SERVICE_DEPLOYMENT_ENVIRONMENT_NAME` | string                                     | minLength=1; maxLength=256; default="default" |
| `service.instance_id`                 | `A13N_SERVICE_SERVICE_INSTANCE_ID`         | string or null                             | default=null                                  |

## `iam`

| Setting                   | Environment variable                   | Type / choices    | Constraints and default               |
| ------------------------- | -------------------------------------- | ----------------- | ------------------------------------- |
| `iam.public_origin`       | `A13N_SERVICE_IAM_PUBLIC_ORIGIN`       | string            | default="http://127.0.0.1:8000"       |
| `iam.initial_admin_email` | `A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL` | string or null    | default=null                          |
| `iam.session_days`        | `A13N_SERVICE_IAM_SESSION_DAYS`        | integer           | minimum=1; maximum=90; default=7      |
| `iam.invitation_days`     | `A13N_SERVICE_IAM_INVITATION_DAYS`     | integer           | minimum=1; maximum=30; default=7      |
| `iam.smtp_host`           | `A13N_SERVICE_IAM_SMTP_HOST`           | string or null    | default=null                          |
| `iam.smtp_port`           | `A13N_SERVICE_IAM_SMTP_PORT`           | integer           | minimum=1; maximum=65535; default=587 |
| `iam.smtp_username`       | `A13N_SERVICE_IAM_SMTP_USERNAME`       | string or null    | default=null                          |
| `iam.smtp_password`       | `A13N_SERVICE_IAM_SMTP_PASSWORD`       | string or null    | default=null                          |
| `iam.smtp_sender`         | `A13N_SERVICE_IAM_SMTP_SENDER`         | string or null    | default=null                          |
| `iam.smtp_tls`            | `A13N_SERVICE_IAM_SMTP_TLS`            | "starttls", "tls" | default="starttls"                    |

## `plugins`

| Setting        | Environment variable       | Type / choices  | Constraints and default  |
| -------------- | -------------------------- | --------------- | ------------------------ |
| `plugins.keys` | `A13N_SERVICE_PLUGIN_KEYS` | array of string | maxItems=128; default=[] |

## `provider_plugins`

| Setting                    | Environment variable                   | Type / choices  | Constraints and default  |
| -------------------------- | -------------------------------------- | --------------- | ------------------------ |
| `provider_plugins.enabled` | `A13N_SERVICE_PROVIDER_PLUGIN_ENABLED` | array of string | maxItems=128; default=[] |

## `worker`

| Setting                        | Environment variable                        | Type / choices | Constraints and default                      |
| ------------------------------ | ------------------------------------------- | -------------- | -------------------------------------------- |
| `worker.concurrency`           | `A13N_SERVICE_WORKER_CONCURRENCY`           | integer        | minimum=1; maximum=1024; default=8           |
| `worker.poll_interval_seconds` | `A13N_SERVICE_WORKER_POLL_INTERVAL_SECONDS` | number         | maximum=60; exclusiveMinimum=0; default=1    |
| `worker.lease_seconds`         | `A13N_SERVICE_WORKER_LEASE_SECONDS`         | number         | minimum=12; maximum=3600; default=30         |
| `worker.cleanup_seconds`       | `A13N_SERVICE_WORKER_CLEANUP_SECONDS`       | number         | maximum=300; exclusiveMinimum=0; default=10  |
| `worker.drain_seconds`         | `A13N_SERVICE_WORKER_DRAIN_SECONDS`         | number         | maximum=3600; exclusiveMinimum=0; default=30 |

## `subagents`

| Setting                                     | Environment variable                                    | Type / choices | Constraints and default                      |
| ------------------------------------------- | ------------------------------------------------------- | -------------- | -------------------------------------------- |
| `subagents.reconcile_drain_seconds`         | `A13N_SERVICE_SUBAGENT_RECONCILE_DRAIN_SECONDS`         | number         | maximum=3600; exclusiveMinimum=0; default=30 |
| `subagents.reconcile_poll_interval_seconds` | `A13N_SERVICE_SUBAGENT_RECONCILE_POLL_INTERVAL_SECONDS` | number         | maximum=60; exclusiveMinimum=0; default=1    |

## `environments`

| Setting                                     | Environment variable                                    | Type / choices  | Constraints and default                      |
| ------------------------------------------- | ------------------------------------------------------- | --------------- | -------------------------------------------- |
| `environments.provider_builtins`            | `A13N_SERVICE_ENVIRONMENT_PROVIDER_BUILTINS`            | array of string | default=["a13n.e2b", "a13n.http-envd"]       |
| `environments.local_providers`              | `A13N_SERVICE_ENVIRONMENT_LOCAL_PROVIDERS`              | object          | —                                            |
| `environments.maintenance_interval_seconds` | `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_INTERVAL_SECONDS` | number          | maximum=300; exclusiveMinimum=0; default=5   |
| `environments.operation_timeout_seconds`    | `A13N_SERVICE_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS`    | number          | maximum=3600; exclusiveMinimum=0; default=60 |
| `environments.max_targets_per_workspace`    | `A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE`    | integer         | minimum=1; default=1000                      |
| `environments.max_active_per_workspace`     | `A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE`     | integer         | minimum=1; default=100                       |
| `environments.maintenance_batch_size`       | `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_BATCH_SIZE`       | integer         | minimum=1; maximum=10000; default=64         |
| `environments.maintenance_concurrency`      | `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_CONCURRENCY`      | integer         | minimum=1; maximum=128; default=4            |

## `pricing`

| Setting               | Environment variable               | Type / choices | Constraints and default |
| --------------------- | ---------------------------------- | -------------- | ----------------------- |
| `pricing.auto_update` | `A13N_SERVICE_PRICING_AUTO_UPDATE` | boolean        | default=true            |

## `observability`

| Setting                                    | Environment variable                                    | Type / choices             | Constraints and default                                     |
| ------------------------------------------ | ------------------------------------------------------- | -------------------------- | ----------------------------------------------------------- |
| `observability.tracing`                    | `A13N_SERVICE_OBSERVABILITY_TRACING`                    | boolean                    | default=true                                                |
| `observability.trace_content`              | `A13N_SERVICE_OBSERVABILITY_TRACE_CONTENT`              | "none", "standard", "full" | default="none"                                              |
| `observability.query.logfire_base_url`     | `A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_BASE_URL`     | string or null             | default=null                                                |
| `observability.query.logfire_read_token`   | `A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_READ_TOKEN`   | string or null             | default=null                                                |
| `observability.query.logfire_history_from` | `A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_HISTORY_FROM` | string or null             | default=null                                                |
| `observability.query.provider`             | `A13N_SERVICE_OBSERVABILITY_QUERY_PROVIDER`             | string                     | maxLength=64; pattern="^[a-z][a-z0-9\_]\*$"; default="none" |
| `observability.query.langfuse_base_url`    | `A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_BASE_URL`    | string or null             | default=null                                                |
| `observability.query.langfuse_public_key`  | `A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_PUBLIC_KEY`  | string or null             | default=null                                                |
| `observability.query.langfuse_secret_key`  | `A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_SECRET_KEY`  | string or null             | default=null                                                |

## `database`

| Setting                              | Environment variable                              | Type / choices | Constraints and default                           |
| ------------------------------------ | ------------------------------------------------- | -------------- | ------------------------------------------------- |
| `database.url`                       | `A13N_SERVICE_DATABASE_URL`                       | string         | format="password"; default="\*\*\*\*\*\*\*\*\*\*" |
| `database.pool_size`                 | `A13N_SERVICE_DATABASE_POOL_SIZE`                 | integer        | minimum=1; maximum=1000; default=10               |
| `database.max_overflow`              | `A13N_SERVICE_DATABASE_MAX_OVERFLOW`              | integer        | minimum=0; maximum=1000; default=20               |
| `database.pool_timeout_seconds`      | `A13N_SERVICE_DATABASE_POOL_TIMEOUT_SECONDS`      | number         | maximum=300; exclusiveMinimum=0; default=30       |
| `database.pool_recycle_seconds`      | `A13N_SERVICE_DATABASE_POOL_RECYCLE_SECONDS`      | integer        | minimum=0; default=3600                           |
| `database.connect_timeout_seconds`   | `A13N_SERVICE_DATABASE_CONNECT_TIMEOUT_SECONDS`   | integer        | minimum=1; maximum=300; default=10                |
| `database.statement_timeout_seconds` | `A13N_SERVICE_DATABASE_STATEMENT_TIMEOUT_SECONDS` | number         | maximum=3600; exclusiveMinimum=0; default=30      |
| `database.cleanup_timeout_seconds`   | `A13N_SERVICE_DATABASE_CLEANUP_TIMEOUT_SECONDS`   | number         | maximum=60; exclusiveMinimum=0; default=5         |
| `database.readiness_timeout_seconds` | `A13N_SERVICE_DATABASE_READINESS_TIMEOUT_SECONDS` | number         | maximum=300; exclusiveMinimum=0; default=3        |

## `models`

| Setting                                  | Environment variable                                 | Type / choices  | Constraints and default                     |
| ---------------------------------------- | ---------------------------------------------------- | --------------- | ------------------------------------------- |
| `models.private_endpoint_domains`        | `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_DOMAINS`        | array of string | default=[]                                  |
| `models.private_endpoint_cidrs`          | `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS`          | array of string | default=[]                                  |
| `models.resolve_dns_on_save`             | `A13N_SERVICE_MODEL_RESOLVE_DNS_ON_SAVE`             | boolean         | default=true                                |
| `models.connection_test_timeout_seconds` | `A13N_SERVICE_MODEL_CONNECTION_TEST_TIMEOUT_SECONDS` | number          | maximum=120; exclusiveMinimum=0; default=15 |

## `memory`

| Setting                  | Environment variable                  | Type / choices | Constraints and default                     |
| ------------------------ | ------------------------------------- | -------------- | ------------------------------------------- |
| `memory.timeout_seconds` | `A13N_SERVICE_MEMORY_TIMEOUT_SECONDS` | number         | maximum=300; exclusiveMinimum=0; default=30 |

## `webhooks`

| Setting                             | Environment variable                            | Type / choices  | Constraints and default                        |
| ----------------------------------- | ----------------------------------------------- | --------------- | ---------------------------------------------- |
| `webhooks.private_endpoint_domains` | `A13N_SERVICE_WEBHOOK_PRIVATE_ENDPOINT_DOMAINS` | array of string | default=[]                                     |
| `webhooks.private_endpoint_cidrs`   | `A13N_SERVICE_WEBHOOK_PRIVATE_ENDPOINT_CIDRS`   | array of string | default=[]                                     |
| `webhooks.poll_interval_seconds`    | `A13N_SERVICE_WEBHOOK_POLL_INTERVAL_SECONDS`    | number          | maximum=60; exclusiveMinimum=0; default=1      |
| `webhooks.claim_lease_seconds`      | `A13N_SERVICE_WEBHOOK_CLAIM_LEASE_SECONDS`      | number          | maximum=3600; exclusiveMinimum=0; default=30   |
| `webhooks.claim_limit`              | `A13N_SERVICE_WEBHOOK_CLAIM_LIMIT`              | integer         | minimum=1; maximum=100; default=25             |
| `webhooks.max_attempts`             | `A13N_SERVICE_WEBHOOK_MAX_ATTEMPTS`             | integer         | minimum=1; maximum=1000; default=10            |
| `webhooks.retry_base_seconds`       | `A13N_SERVICE_WEBHOOK_RETRY_BASE_SECONDS`       | number          | maximum=3600; exclusiveMinimum=0; default=2    |
| `webhooks.retry_max_seconds`        | `A13N_SERVICE_WEBHOOK_RETRY_MAX_SECONDS`        | number          | maximum=86400; exclusiveMinimum=0; default=300 |
| `webhooks.request_timeout_seconds`  | `A13N_SERVICE_WEBHOOK_REQUEST_TIMEOUT_SECONDS`  | number          | maximum=300; exclusiveMinimum=0; default=10    |
| `webhooks.max_response_bytes`       | `A13N_SERVICE_WEBHOOK_MAX_RESPONSE_BYTES`       | integer         | minimum=1; maximum=16777216; default=65536     |

## `lifecycle`

| Setting                                       | Environment variable                                       | Type / choices | Constraints and default                        |
| --------------------------------------------- | ---------------------------------------------------------- | -------------- | ---------------------------------------------- |
| `lifecycle.retention_days`                    | `A13N_SERVICE_LIFECYCLE_RETENTION_DAYS`                    | integer        | minimum=1; maximum=3650; default=30            |
| `lifecycle.published_delivery_retention_days` | `A13N_SERVICE_LIFECYCLE_PUBLISHED_DELIVERY_RETENTION_DAYS` | integer        | minimum=1; maximum=3650; default=7             |
| `lifecycle.dead_letter_retention_days`        | `A13N_SERVICE_LIFECYCLE_DEAD_LETTER_RETENTION_DAYS`        | integer        | minimum=1; maximum=3650; default=30            |
| `lifecycle.retention_poll_interval_seconds`   | `A13N_SERVICE_LIFECYCLE_RETENTION_POLL_INTERVAL_SECONDS`   | number         | maximum=86400; exclusiveMinimum=0; default=300 |
| `lifecycle.retention_batch_limit`             | `A13N_SERVICE_LIFECYCLE_RETENTION_BATCH_LIMIT`             | integer        | minimum=1; maximum=1000; default=200           |
| `lifecycle.projection_poll_interval_seconds`  | `A13N_SERVICE_LIFECYCLE_PROJECTION_POLL_INTERVAL_SECONDS`  | number         | maximum=60; exclusiveMinimum=0; default=1      |
| `lifecycle.projection_lease_seconds`          | `A13N_SERVICE_LIFECYCLE_PROJECTION_LEASE_SECONDS`          | number         | maximum=3600; exclusiveMinimum=0; default=60   |
| `lifecycle.projection_retry_seconds`          | `A13N_SERVICE_LIFECYCLE_PROJECTION_RETRY_SECONDS`          | number         | minimum=0; maximum=3600; default=5             |
| `lifecycle.projection_max_attempts`           | `A13N_SERVICE_LIFECYCLE_PROJECTION_MAX_ATTEMPTS`           | integer        | minimum=1; maximum=1000; default=20            |
| `lifecycle.projection_claim_limit`            | `A13N_SERVICE_LIFECYCLE_PROJECTION_CLAIM_LIMIT`            | integer        | minimum=1; maximum=200; default=16             |

## `control`

| Setting                                    | Environment variable                                    | Type / choices | Constraints and default                        |
| ------------------------------------------ | ------------------------------------------------------- | -------------- | ---------------------------------------------- |
| `control.recovery_poll_interval_seconds`   | `A13N_SERVICE_CONTROL_RECOVERY_POLL_INTERVAL_SECONDS`   | number         | maximum=300; exclusiveMinimum=0; default=1     |
| `control.recovery_batch_limit`             | `A13N_SERVICE_CONTROL_RECOVERY_BATCH_LIMIT`             | integer        | minimum=1; maximum=1000; default=64            |
| `control.recovery_item_timeout_seconds`    | `A13N_SERVICE_CONTROL_RECOVERY_ITEM_TIMEOUT_SECONDS`    | number         | maximum=300; exclusiveMinimum=0; default=30    |
| `control.collection_poll_interval_seconds` | `A13N_SERVICE_CONTROL_COLLECTION_POLL_INTERVAL_SECONDS` | number         | maximum=86400; exclusiveMinimum=0; default=300 |
| `control.collection_batch_limit`           | `A13N_SERVICE_CONTROL_COLLECTION_BATCH_LIMIT`           | integer        | minimum=1; maximum=1000; default=64            |
| `control.collection_timeout_seconds`       | `A13N_SERVICE_CONTROL_COLLECTION_TIMEOUT_SECONDS`       | number         | maximum=300; exclusiveMinimum=0; default=30    |

## `assets`

| Setting                                   | Environment variable                                  | Type / choices | Constraints and default                                   |
| ----------------------------------------- | ----------------------------------------------------- | -------------- | --------------------------------------------------------- |
| `assets.tombstone_minimum_retention_days` | `A13N_SERVICE_ASSET_TOMBSTONE_MINIMUM_RETENTION_DAYS` | integer        | minimum=1; maximum=36500; default=30                      |
| `assets.max_size_bytes`                   | `A13N_SERVICE_ASSET_MAX_SIZE_BYTES`                   | integer        | minimum=1; maximum=9223372036854775807; default=104857600 |
| `assets.cleanup_poll_interval_seconds`    | `A13N_SERVICE_ASSET_CLEANUP_POLL_INTERVAL_SECONDS`    | number         | maximum=300; exclusiveMinimum=0; default=5                |
| `assets.cleanup_lease_seconds`            | `A13N_SERVICE_ASSET_CLEANUP_LEASE_SECONDS`            | number         | maximum=3600; exclusiveMinimum=0; default=30              |
| `assets.cleanup_max_attempts`             | `A13N_SERVICE_ASSET_CLEANUP_MAX_ATTEMPTS`             | integer        | minimum=1; maximum=1000; default=10                       |

## `hooks`

| Setting                                | Environment variable                               | Type / choices | Constraints and default             |
| -------------------------------------- | -------------------------------------------------- | -------------- | ----------------------------------- |
| `hooks.history_minimum_retention_days` | `A13N_SERVICE_HOOK_HISTORY_MINIMUM_RETENTION_DAYS` | integer        | minimum=1; maximum=3650; default=30 |

## `objects`

| Setting                                 | Environment variable                                | Type / choices | Constraints and default                            |
| --------------------------------------- | --------------------------------------------------- | -------------- | -------------------------------------------------- |
| `objects.publication_timeout_seconds`   | `A13N_SERVICE_OBJECT_PUBLICATION_TIMEOUT_SECONDS`   | number         | maximum=3600; exclusiveMinimum=0; default=120      |
| `objects.orphan_minimum_age_hours`      | `A13N_SERVICE_OBJECT_ORPHAN_MINIMUM_AGE_HOURS`      | integer        | minimum=1; maximum=87600; default=24               |
| `objects.backend`                       | `A13N_SERVICE_OBJECT_BACKEND`                       | "s3", "local"  | default="local"                                    |
| `objects.local_root`                    | `A13N_SERVICE_OBJECT_LOCAL_ROOT`                    | string         | format="path"; default="var/objects"               |
| `objects.bucket`                        | `A13N_SERVICE_OBJECT_BUCKET`                        | string or null | default=null                                       |
| `objects.region`                        | `A13N_SERVICE_OBJECT_REGION`                        | string         | minLength=1; default="us-east-1"                   |
| `objects.endpoint_url`                  | `A13N_SERVICE_OBJECT_ENDPOINT_URL`                  | string or null | default=null                                       |
| `objects.force_path_style`              | `A13N_SERVICE_OBJECT_FORCE_PATH_STYLE`              | boolean        | default=false                                      |
| `objects.connect_timeout_seconds`       | `A13N_SERVICE_OBJECT_CONNECT_TIMEOUT_SECONDS`       | number         | maximum=300; exclusiveMinimum=0; default=5         |
| `objects.read_timeout_seconds`          | `A13N_SERVICE_OBJECT_READ_TIMEOUT_SECONDS`          | number         | maximum=300; exclusiveMinimum=0; default=30        |
| `objects.compatibility_timeout_seconds` | `A13N_SERVICE_OBJECT_COMPATIBILITY_TIMEOUT_SECONDS` | number         | maximum=600; exclusiveMinimum=0; default=120       |
| `objects.max_pool_connections`          | `A13N_SERVICE_OBJECT_MAX_POOL_CONNECTIONS`          | integer        | minimum=1; maximum=1000; default=20                |
| `objects.multipart_part_size`           | `A13N_SERVICE_OBJECT_MULTIPART_PART_SIZE`           | integer        | minimum=5242880; maximum=67108864; default=8388608 |
| `objects.local_chunk_size`              | `A13N_SERVICE_OBJECT_LOCAL_CHUNK_SIZE`              | integer        | minimum=4096; maximum=8388608; default=262144      |

## `runs`

| Setting                          | Environment variable                         | Type / choices | Constraints and default                            |
| -------------------------------- | -------------------------------------------- | -------------- | -------------------------------------------------- |
| `runs.stream_max_events`         | `A13N_SERVICE_RUN_STREAM_MAX_EVENTS`         | integer        | minimum=1; maximum=100000; default=4096            |
| `runs.stream_max_event_bytes`    | `A13N_SERVICE_RUN_STREAM_MAX_EVENT_BYTES`    | integer        | minimum=1024; maximum=16777216; default=327680     |
| `runs.stream_closed_ttl_seconds` | `A13N_SERVICE_RUN_STREAM_CLOSED_TTL_SECONDS` | integer        | minimum=60; maximum=31536000; default=86400        |
| `runs.replay_max_events`         | `A13N_SERVICE_RUN_REPLAY_MAX_EVENTS`         | integer        | minimum=1; maximum=100000; default=4096            |
| `runs.replay_max_items`          | `A13N_SERVICE_RUN_REPLAY_MAX_ITEMS`          | integer        | minimum=1; maximum=100000; default=2048            |
| `runs.replay_max_bytes`          | `A13N_SERVICE_RUN_REPLAY_MAX_BYTES`          | integer        | minimum=1024; maximum=1073741824; default=16777216 |

## `gateway`

| Setting                                           | Environment variable                                           | Type / choices | Constraints and default                                          |
| ------------------------------------------------- | -------------------------------------------------------------- | -------------- | ---------------------------------------------------------------- |
| `gateway.stream_page_size`                        | `A13N_SERVICE_GATEWAY_STREAM_PAGE_SIZE`                        | integer        | minimum=1; maximum=1000; default=256                             |
| `gateway.stream_poll_interval_seconds`            | `A13N_SERVICE_GATEWAY_STREAM_POLL_INTERVAL_SECONDS`            | number         | maximum=10; exclusiveMinimum=0; default=0.25                     |
| `gateway.stream_heartbeat_interval_seconds`       | `A13N_SERVICE_GATEWAY_STREAM_HEARTBEAT_INTERVAL_SECONDS`       | number         | maximum=300; exclusiveMinimum=0; default=15                      |
| `gateway.stream_authorization_interval_seconds`   | `A13N_SERVICE_GATEWAY_STREAM_AUTHORIZATION_INTERVAL_SECONDS`   | number         | maximum=300; exclusiveMinimum=0; default=30                      |
| `gateway.stream_maximum_lifetime_seconds`         | `A13N_SERVICE_GATEWAY_STREAM_MAXIMUM_LIFETIME_SECONDS`         | number         | maximum=3600; exclusiveMinimum=0; default=300                    |
| `gateway.notification_poll_interval_seconds`      | `A13N_SERVICE_GATEWAY_NOTIFICATION_POLL_INTERVAL_SECONDS`      | number         | maximum=30; exclusiveMinimum=0; default=0.5                      |
| `gateway.notification_heartbeat_interval_seconds` | `A13N_SERVICE_GATEWAY_NOTIFICATION_HEARTBEAT_INTERVAL_SECONDS` | number         | maximum=300; exclusiveMinimum=0; default=20                      |
| `gateway.notification_send_timeout_seconds`       | `A13N_SERVICE_GATEWAY_NOTIFICATION_SEND_TIMEOUT_SECONDS`       | number         | maximum=60; exclusiveMinimum=0; default=10                       |
| `gateway.notification_max_frame_bytes`            | `A13N_SERVICE_GATEWAY_NOTIFICATION_MAX_FRAME_BYTES`            | integer        | minimum=1024; maximum=1048576; default=65536                     |
| `gateway.notification_max_subscriptions`          | `A13N_SERVICE_GATEWAY_NOTIFICATION_MAX_SUBSCRIPTIONS`          | integer        | minimum=1; maximum=1024; default=64                              |
| `gateway.notification_max_topics`                 | `A13N_SERVICE_GATEWAY_NOTIFICATION_MAX_TOPICS`                 | integer        | minimum=1; maximum=4096; default=128                             |
| `gateway.notification_poll_limit`                 | `A13N_SERVICE_GATEWAY_NOTIFICATION_POLL_LIMIT`                 | integer        | minimum=1; maximum=1000; default=100                             |
| `gateway.notification_maximum_lifetime_seconds`   | `A13N_SERVICE_GATEWAY_NOTIFICATION_MAXIMUM_LIFETIME_SECONDS`   | number         | maximum=86400; exclusiveMinimum=0; default=3600                  |
| `gateway.run_execution_max_attempts`              | `A13N_SERVICE_GATEWAY_RUN_EXECUTION_MAX_ATTEMPTS`              | integer        | minimum=0; maximum=100; default=3                                |
| `gateway.run_max_handoffs`                        | `A13N_SERVICE_GATEWAY_RUN_MAX_HANDOFFS`                        | integer        | minimum=0; maximum=100; default=2                                |
| `gateway.run_queue_name`                          | `A13N_SERVICE_GATEWAY_RUN_QUEUE_NAME`                          | string         | pattern="^[A-Za-z\_][A-Za-z0-9\_.:-]{0,127}$"; default="default" |
| `gateway.run_priority`                            | `A13N_SERVICE_GATEWAY_RUN_PRIORITY`                            | integer        | minimum=-1000000; maximum=1000000; default=0                     |
| `gateway.a2a_enabled`                             | `A13N_SERVICE_A2A_ENABLED`                                     | boolean        | default=true                                                     |
| `gateway.a2a_public_origin`                       | `A13N_SERVICE_A2A_PUBLIC_ORIGIN`                               | string or null | default=null                                                     |
| `gateway.a2a_default_agent_id`                    | `A13N_SERVICE_A2A_DEFAULT_AGENT_ID`                            | string or null | default=null                                                     |
| `gateway.a2a_poll_interval_seconds`               | `A13N_SERVICE_A2A_POLL_INTERVAL_SECONDS`                       | number         | maximum=30; exclusiveMinimum=0; default=0.5                      |
| `gateway.a2a_maximum_wait_seconds`                | `A13N_SERVICE_A2A_MAXIMUM_WAIT_SECONDS`                        | number         | maximum=3600; exclusiveMinimum=0; default=300                    |

## `secrets`

| Setting                     | Environment variable                    | Type / choices | Constraints and default |
| --------------------------- | --------------------------------------- | -------------- | ----------------------- |
| `secrets.master_key_base64` | `A13N_SERVICE_SECRET_MASTER_KEY_BASE64` | string or null | default=null            |
| `secrets.encryption_key_id` | `A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID` | string or null | default=null            |

## `connectivity`

| Setting                                                  | Environment variable                                                  | Type / choices             | Constraints and default                                    |
| -------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------- | ---------------------------------------------------------- |
| `connectivity.provider_request_max_bytes`                | `A13N_SERVICE_CONNECTIVITY_PROVIDER_REQUEST_MAX_BYTES`                | integer                    | minimum=1; maximum=8388608; default=8388608                |
| `connectivity.workspace_pending_max_count`               | `A13N_SERVICE_CONNECTIVITY_WORKSPACE_PENDING_MAX_COUNT`               | integer                    | minimum=1; maximum=1000000; default=10000                  |
| `connectivity.workspace_pending_max_bytes`               | `A13N_SERVICE_CONNECTIVITY_WORKSPACE_PENDING_MAX_BYTES`               | integer                    | minimum=1; maximum=9223372036854775807; default=1073741824 |
| `connectivity.account_pending_max_count`                 | `A13N_SERVICE_CONNECTIVITY_ACCOUNT_PENDING_MAX_COUNT`                 | integer                    | minimum=1; maximum=100000; default=1000                    |
| `connectivity.account_pending_max_bytes`                 | `A13N_SERVICE_CONNECTIVITY_ACCOUNT_PENDING_MAX_BYTES`                 | integer                    | minimum=1; maximum=9223372036854775807; default=134217728  |
| `connectivity.batch_max_events`                          | `A13N_SERVICE_CONNECTIVITY_BATCH_MAX_EVENTS`                          | integer                    | minimum=1; maximum=1000; default=100                       |
| `connectivity.batch_max_bytes`                           | `A13N_SERVICE_CONNECTIVITY_BATCH_MAX_BYTES`                           | integer                    | minimum=1; maximum=67108864; default=4194304               |
| `connectivity.batch_max_wait_seconds`                    | `A13N_SERVICE_CONNECTIVITY_BATCH_MAX_WAIT_SECONDS`                    | number                     | maximum=3600; exclusiveMinimum=0; default=300              |
| `connectivity.admission_poll_interval_seconds`           | `A13N_SERVICE_CONNECTIVITY_ADMISSION_POLL_INTERVAL_SECONDS`           | number                     | maximum=60; exclusiveMinimum=0; default=1                  |
| `connectivity.admission_lease_seconds`                   | `A13N_SERVICE_CONNECTIVITY_ADMISSION_LEASE_SECONDS`                   | number                     | maximum=3600; exclusiveMinimum=0; default=30               |
| `connectivity.admission_backoff_steps`                   | `A13N_SERVICE_CONNECTIVITY_ADMISSION_BACKOFF_STEPS`                   | integer                    | minimum=1; maximum=1000; default=20                        |
| `connectivity.admission_max_backoff_seconds`             | `A13N_SERVICE_CONNECTIVITY_ADMISSION_MAX_BACKOFF_SECONDS`             | number                     | maximum=3600; exclusiveMinimum=0; default=300              |
| `connectivity.dedup_horizon_seconds`                     | `A13N_SERVICE_CONNECTIVITY_DEDUP_HORIZON_SECONDS`                     | integer                    | minimum=60; maximum=2592000; default=604800                |
| `connectivity.connect_timeout_seconds`                   | `A13N_SERVICE_CONNECTIVITY_CONNECT_TIMEOUT_SECONDS`                   | number                     | maximum=60; exclusiveMinimum=0; default=5                  |
| `connectivity.read_timeout_seconds`                      | `A13N_SERVICE_CONNECTIVITY_READ_TIMEOUT_SECONDS`                      | number                     | maximum=300; exclusiveMinimum=0; default=30                |
| `connectivity.total_timeout_seconds`                     | `A13N_SERVICE_CONNECTIVITY_TOTAL_TIMEOUT_SECONDS`                     | number                     | maximum=600; exclusiveMinimum=0; default=60                |
| `connectivity.response_max_bytes`                        | `A13N_SERVICE_CONNECTIVITY_RESPONSE_MAX_BYTES`                        | integer                    | minimum=1; maximum=8388608; default=1048576                |
| `connectivity.max_redirects`                             | `A13N_SERVICE_CONNECTIVITY_MAX_REDIRECTS`                             | integer                    | minimum=0; maximum=3; default=3                            |
| `connectivity.oauth_setup_ttl_seconds`                   | `A13N_SERVICE_CONNECTIVITY_OAUTH_SETUP_TTL_SECONDS`                   | integer                    | minimum=60; maximum=900; default=600                       |
| `connectivity.public_origin`                             | `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN`                             | string or null             | default=null                                               |
| `connectivity.authorization_callback_urls`               | `A13N_SERVICE_CONNECTIVITY_AUTHORIZATION_CALLBACK_URLS`               | array of string            | default=[]                                                 |
| `connectivity.mcp_servers`                               | `A13N_SERVICE_CONNECTIVITY_MCP_SERVERS`                               | array of MCPServerSettings | default=[]                                                 |
| `connectivity.oauth_client_name`                         | `A13N_SERVICE_CONNECTIVITY_OAUTH_CLIENT_NAME`                         | string                     | minLength=1; maxLength=128; default="Agent Foundation"     |
| `connectivity.private_endpoint_domains`                  | `A13N_SERVICE_CONNECTIVITY_PRIVATE_ENDPOINT_DOMAINS`                  | array of string            | default=[]                                                 |
| `connectivity.private_endpoint_cidrs`                    | `A13N_SERVICE_CONNECTIVITY_PRIVATE_ENDPOINT_CIDRS`                    | array of string            | default=[]                                                 |
| `connectivity.http_origins`                              | `A13N_SERVICE_CONNECTIVITY_HTTP_ORIGINS`                              | array of string            | default=[]                                                 |
| `connectivity.provider_origins`                          | `A13N_SERVICE_CONNECTIVITY_PROVIDER_ORIGINS`                          | array of string            | default=[]                                                 |
| `connectivity.provider_token_expiry_skew_seconds`        | `A13N_SERVICE_CONNECTIVITY_PROVIDER_TOKEN_EXPIRY_SKEW_SECONDS`        | integer                    | minimum=0; maximum=600; default=60                         |
| `connectivity.setup_correlation_secret`                  | `A13N_SERVICE_CONNECTIVITY_SETUP_CORRELATION_SECRET`                  | string or null             | default=null                                               |
| `connectivity.connector_reconcile_poll_interval_seconds` | `A13N_SERVICE_CONNECTIVITY_CONNECTOR_RECONCILE_POLL_INTERVAL_SECONDS` | number                     | maximum=300; exclusiveMinimum=0; default=2                 |
| `connectivity.connector_reconcile_lease_seconds`         | `A13N_SERVICE_CONNECTIVITY_CONNECTOR_RECONCILE_LEASE_SECONDS`         | integer                    | minimum=10; maximum=600; default=60                        |
| `connectivity.retention_poll_interval_seconds`           | `A13N_SERVICE_CONNECTIVITY_RETENTION_POLL_INTERVAL_SECONDS`           | number                     | maximum=3600; exclusiveMinimum=0; default=60               |
| `connectivity.retention_batch_size`                      | `A13N_SERVICE_CONNECTIVITY_RETENTION_BATCH_SIZE`                      | integer                    | minimum=1; maximum=1000; default=25                        |

## `redis`

| Setting                               | Environment variable                               | Type / choices    | Constraints and default                      |
| ------------------------------------- | -------------------------------------------------- | ----------------- | -------------------------------------------- |
| `redis.backend`                       | `A13N_SERVICE_REDIS_BACKEND`                       | "redis", "memory" | default="redis"                              |
| `redis.url`                           | `A13N_SERVICE_REDIS_URL`                           | string or null    | default="\*\*\*\*\*\*\*\*\*\*"               |
| `redis.max_connections`               | `A13N_SERVICE_REDIS_MAX_CONNECTIONS`               | integer           | minimum=1; maximum=1000; default=20          |
| `redis.connect_timeout_seconds`       | `A13N_SERVICE_REDIS_CONNECT_TIMEOUT_SECONDS`       | number            | maximum=300; exclusiveMinimum=0; default=5   |
| `redis.command_timeout_seconds`       | `A13N_SERVICE_REDIS_COMMAND_TIMEOUT_SECONDS`       | number            | maximum=300; exclusiveMinimum=0; default=10  |
| `redis.health_check_interval_seconds` | `A13N_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | number            | maximum=3600; exclusiveMinimum=0; default=30 |
| `redis.cleanup_timeout_seconds`       | `A13N_SERVICE_REDIS_CLEANUP_TIMEOUT_SECONDS`       | number            | maximum=60; exclusiveMinimum=0; default=5    |

## `filesystem`

| Setting                   | Environment variable                   | Type / choices | Constraints and default            |
| ------------------------- | -------------------------------------- | -------------- | ---------------------------------- |
| `filesystem.root`         | `A13N_SERVICE_FILESYSTEM_ROOT`         | string         | format="path"; default="var/files" |
| `filesystem.worker_limit` | `A13N_SERVICE_FILESYSTEM_WORKER_LIMIT` | integer        | minimum=1; maximum=256; default=20 |

## `migration`

| Setting                                      | Environment variable                                      | Type / choices | Constraints and default                        |
| -------------------------------------------- | --------------------------------------------------------- | -------------- | ---------------------------------------------- |
| `migration.auto_migrate`                     | `A13N_SERVICE_AUTO_MIGRATE`                               | boolean        | default=false                                  |
| `migration.advisory_lock_timeout_seconds`    | `A13N_SERVICE_MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS`    | number         | maximum=86400; exclusiveMinimum=0; default=900 |
| `migration.lock_timeout_seconds`             | `A13N_SERVICE_MIGRATION_LOCK_TIMEOUT_SECONDS`             | number         | maximum=3600; exclusiveMinimum=0; default=3    |
| `migration.statement_timeout_seconds`        | `A13N_SERVICE_MIGRATION_STATEMENT_TIMEOUT_SECONDS`        | number         | maximum=86400; exclusiveMinimum=0; default=900 |
| `migration.idle_transaction_timeout_seconds` | `A13N_SERVICE_MIGRATION_IDLE_TRANSACTION_TIMEOUT_SECONDS` | number         | maximum=3600; exclusiveMinimum=0; default=30   |

## `logging`

| Setting          | Environment variable      | Type / choices   | Constraints and default |
| ---------------- | ------------------------- | ---------------- | ----------------------- |
| `logging.level`  | `A13N_SERVICE_LOG_LEVEL`  | string           | default="INFO"          |
| `logging.format` | `A13N_SERVICE_LOG_FORMAT` | "pretty", "json" | default="pretty"        |
