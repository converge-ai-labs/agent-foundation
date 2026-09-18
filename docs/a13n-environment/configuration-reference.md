# Provider configuration reference

Generated from the current built-in Provider Pydantic models by `scripts/docs/references.py`. Do not independently edit the rows. Use [Configure Providers](configuration.md) for authoring, configuration/runtime/state boundaries, and cross-field restrictions. These are Provider settings, not standalone daemon JSON defaults.

Required means no default. Fields backed by a factory have a model-computed default; no Host environment or credential store is read while generating this page. Named schema sections below include nested roots, mounts, and shell profiles. Runtime clients and authoritative target state do not belong in these template configuration objects.

## Cloud providers

All six cloud providers use the same configuration, backend, and private-credential boundaries. Their schemas are peer entries below; capability differences remain in the [cloud provider guide](providers.md#cloud-providers).

| Provider       | Recipe                                                | Backend                                                     | Credential                          |
| -------------- | ----------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------- |
| E2B            | [E2BProviderConfiguration](#e2bproviderconfiguration) | [E2BBackendConfiguration](#e2bbackendconfiguration)         | [E2BCredential](#e2bcredential)     |
| Daytona        | [DaytonaConfiguration](#daytonaconfiguration)         | [DaytonaBackendConfiguration](#daytonabackendconfiguration) | [TokenCredential](#tokencredential) |
| Modal          | [ModalConfiguration](#modalconfiguration)             | [ModalBackendConfiguration](#modalbackendconfiguration)     | [ModalCredential](#modalcredential) |
| Vercel Sandbox | [VercelConfiguration](#vercelconfiguration)           | [VercelBackendConfiguration](#vercelbackendconfiguration)   | [TokenCredential](#tokencredential) |
| Fly.io Sprites | [SpritesConfiguration](#spritesconfiguration)         | [SpritesBackendConfiguration](#spritesbackendconfiguration) | [TokenCredential](#tokencredential) |
| Runloop        | [RunloopConfiguration](#runloopconfiguration)         | [RunloopBackendConfiguration](#runloopbackendconfiguration) | [TokenCredential](#tokencredential) |

## `DirectLocalProviderConfiguration`

| Field                      | Required | Type / choices                   | Constraints and default                 |
| -------------------------- | -------- | -------------------------------- | --------------------------------------- |
| `root`                     | true     | DirectLocalRootConfiguration     | —                                       |
| `shell_profiles`           | false    | array of DirectLocalShellProfile | default=[]                              |
| `allowed_executables`      | false    | array of string                  | uniqueItems=true; default=[]            |
| `inherit_environment`      | false    | boolean                          | default=false                           |
| `allowed_environment_keys` | false    | array of string or null          | default=[]                              |
| `allowed_ports`            | false    | array of integer                 | uniqueItems=true; default=[]            |
| `max_value_bytes`          | false    | integer                          | exclusiveMinimum=0; default=67108864    |
| `max_concurrent_processes` | false    | integer                          | exclusiveMinimum=0; default=128         |
| `max_wall_time_seconds`    | false    | number                           | default=86400                           |
| `terminate_grace_seconds`  | false    | number                           | default=5.0                             |
| `max_buffer_bytes`         | false    | integer                          | exclusiveMinimum=0; default=1048576     |
| `max_spool_bytes`          | false    | integer                          | exclusiveMinimum=0; default=68719476736 |

## `DirectLocalRootConfiguration`

| Field       | Required | Type / choices | Constraints and default |
| ----------- | -------- | -------------- | ----------------------- |
| `path`      | true     | string         | format="path"           |
| `read_only` | false    | boolean        | default=false           |

## `DirectLocalShellProfile`

| Field             | Required | Type / choices        | Constraints and default    |
| ----------------- | -------- | --------------------- | -------------------------- |
| `profile_id`      | true     | string                | minLength=1; maxLength=128 |
| `executable`      | true     | string                | format="path"              |
| `fixed_arguments` | false    | array of string       | default=[]                 |
| `dialect`         | false    | "posix", "powershell" | default="posix"            |
| `allow_login`     | false    | boolean               | default=false              |

## `LocalEnvdProviderConfiguration`

| Field                         | Required | Type / choices                  | Constraints and default                             |
| ----------------------------- | -------- | ------------------------------- | --------------------------------------------------- |
| `workspace`                   | true     | LocalEnvdWorkspaceConfiguration | —                                                   |
| `execution_network`           | false    | "host", "deny"                  | default="host"                                      |
| `trusted_executable_roots`    | false    | array of string                 | default=[]                                          |
| `shell_profiles`              | false    | array of LocalEnvdShellProfile  | default=[]                                          |
| `max_file_bytes`              | false    | integer                         | exclusiveMinimum=0; default=16777216                |
| `max_output_preview_bytes`    | false    | integer                         | maximum=16777216; exclusiveMinimum=0; default=65536 |
| `max_output_bytes_per_stream` | false    | integer                         | exclusiveMinimum=0; default=1073741824              |
| `max_spool_bytes`             | false    | integer                         | exclusiveMinimum=0; default=68719476736             |

## `LocalEnvdNetworkMode`

Choices: `"host", "deny"`.

## `LocalEnvdShellProfile`

| Field              | Required | Type / choices  | Constraints and default             |
| ------------------ | -------- | --------------- | ----------------------------------- |
| `profile_id`       | true     | string          | minLength=1; maxLength=128          |
| `executable`       | true     | string          | format="path"                       |
| `fixed_arguments`  | false    | array of string | default=[]                          |
| `allow_login`      | false    | boolean         | default=false                       |
| `max_script_bytes` | false    | integer         | exclusiveMinimum=0; default=1048576 |

## `LocalEnvdWorkspaceConfiguration`

| Field       | Required | Type / choices | Constraints and default |
| ----------- | -------- | -------------- | ----------------------- |
| `path`      | true     | string         | format="path"           |
| `read_only` | false    | boolean        | default=false           |

## `DockerProviderConfiguration`

| Field                         | Required | Type / choices                    | Constraints and default                                                                     |
| ----------------------------- | -------- | --------------------------------- | ------------------------------------------------------------------------------------------- |
| `image`                       | false    | string                            | minLength=1; maxLength=1024; default="ghcr.io/converge-ai-labs/a13n-docker-environment:dev" |
| `mounts`                      | false    | array of DockerMountConfiguration | default=[]                                                                                  |
| `environment`                 | false    | object                            | —; default from model factory                                                               |
| `init_script`                 | false    | string or null                    | format="multiline"; default=null                                                            |
| `disable_network`             | false    | boolean                           | default=false                                                                               |
| `user`                        | false    | string or null                    | default=null                                                                                |
| `shell`                       | false    | string                            | default="/bin/sh"                                                                           |
| `python`                      | false    | string                            | default="python3"                                                                           |
| `cpus`                        | false    | number or null                    | default=null                                                                                |
| `memory_gb`                   | false    | number or null                    | default=null                                                                                |
| `pids_limit`                  | false    | integer or null                   | default=null                                                                                |
| `stop_grace_seconds`          | false    | integer                           | minimum=0; maximum=300; default=10                                                          |
| `request_timeout_seconds`     | false    | integer                           | maximum=3600; exclusiveMinimum=0; default=60                                                |
| `max_file_bytes`              | false    | integer                           | exclusiveMinimum=0; default=16777216                                                        |
| `max_query_entries`           | false    | integer                           | exclusiveMinimum=0; default=100000                                                          |
| `max_output_preview_bytes`    | false    | integer                           | exclusiveMinimum=0; default=65536                                                           |
| `max_output_bytes_per_stream` | false    | integer                           | exclusiveMinimum=0; default=16777216                                                        |
| `max_spool_bytes`             | false    | integer                           | exclusiveMinimum=0; default=67108864                                                        |
| `max_concurrent_processes`    | false    | integer                           | exclusiveMinimum=0; default=128                                                             |

## `DockerMountConfiguration`

| Field       | Required | Type / choices | Constraints and default |
| ----------- | -------- | -------------- | ----------------------- |
| `source`    | true     | string         | —                       |
| `target`    | true     | string         | format="path"           |
| `read_only` | false    | boolean        | default=true            |

## `E2BProviderConfiguration`

| Field                       | Required | Type / choices | Constraints and default                                   |
| --------------------------- | -------- | -------------- | --------------------------------------------------------- |
| `template`                  | false    | string         | minLength=1; maxLength=256; default="base"                |
| `root`                      | false    | string         | default="/home/user"                                      |
| `user`                      | false    | string         | `pattern="^[a-z_][a-z0-9_-]{0,63}$"`; default="user"      |
| `python`                    | false    | string         | default="/usr/bin/python3"                                |
| `timeout_seconds`           | false    | integer        | minimum=30; maximum=86400; default=3600                   |
| `request_timeout_seconds`   | false    | number         | maximum=300; exclusiveMinimum=0; default=30               |
| `allow_internet_access`     | false    | boolean        | default=true                                              |
| `read_only`                 | false    | boolean        | default=false                                             |
| `max_file_bytes`            | false    | integer        | maximum=1073741824; exclusiveMinimum=0; default=16777216  |
| `max_observation_bytes`     | false    | integer        | maximum=16777216; exclusiveMinimum=0; default=1048576     |
| `max_active_observations`   | false    | integer        | maximum=1024; exclusiveMinimum=0; default=128             |
| `max_retained_output_bytes` | false    | integer        | maximum=1073741824; exclusiveMinimum=0; default=134217728 |
| `max_query_entries`         | false    | integer        | maximum=100000; exclusiveMinimum=0; default=10000         |

## `DaytonaConfiguration`

| Field                     | Required | Type / choices | Constraints and default                                |
| ------------------------- | -------- | -------------- | ------------------------------------------------------ |
| `root`                    | false    | string         | default="/home/daytona"                                |
| `python`                  | false    | string         | default="python3"                                      |
| `shell`                   | false    | string         | default="/bin/bash"                                    |
| `read_only`               | false    | boolean        | default=false                                          |
| `request_timeout_seconds` | false    | number         | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer        | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer        | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer        | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `snapshot`                | false    | string or null | default=null                                           |
| `cpu`                     | false    | integer        | minimum=1; maximum=32; default=2                       |
| `memory`                  | false    | integer        | minimum=1; maximum=128; default=4                      |
| `disk`                    | false    | integer        | minimum=1; maximum=1024; default=10                    |

## `ModalConfiguration`

| Field                     | Required | Type / choices | Constraints and default                                |
| ------------------------- | -------- | -------------- | ------------------------------------------------------ |
| `root`                    | false    | string         | default="/"                                            |
| `python`                  | false    | string         | default="/usr/local/bin/python3"                       |
| `shell`                   | false    | string         | default="/bin/bash"                                    |
| `read_only`               | false    | boolean        | default=false                                          |
| `request_timeout_seconds` | false    | number         | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer        | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer        | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer        | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `image`                   | false    | string         | minLength=1; maxLength=256; default="python:3.13-slim" |
| `timeout_seconds`         | false    | integer        | minimum=300; maximum=86400; default=86400              |
| `cpu`                     | false    | number         | maximum=64; exclusiveMinimum=0; default=1              |
| `memory`                  | false    | integer        | minimum=128; maximum=262144; default=1024              |

## `VercelConfiguration`

| Field                     | Required | Type / choices | Constraints and default                                |
| ------------------------- | -------- | -------------- | ------------------------------------------------------ |
| `root`                    | false    | string         | default="/vercel/sandbox"                              |
| `python`                  | false    | string         | default="/vercel/runtimes/python/bin/python3"          |
| `shell`                   | false    | string         | default="/bin/bash"                                    |
| `read_only`               | false    | boolean        | default=false                                          |
| `request_timeout_seconds` | false    | number         | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer        | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer        | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer        | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `runtime`                 | false    | string         | minLength=1; maxLength=128; default="python3.13"       |
| `timeout_seconds`         | false    | integer        | minimum=300; maximum=18000; default=3600               |
| `vcpus`                   | false    | integer        | minimum=2; maximum=8; default=2                        |

## `SpritesConfiguration`

| Field                     | Required | Type / choices | Constraints and default                                |
| ------------------------- | -------- | -------------- | ------------------------------------------------------ |
| `root`                    | false    | string         | default="/home/sprite"                                 |
| `python`                  | false    | string         | default="python3"                                      |
| `shell`                   | false    | string         | default="/bin/bash"                                    |
| `read_only`               | false    | boolean        | default=false                                          |
| `request_timeout_seconds` | false    | number         | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer        | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer        | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer        | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `region`                  | false    | string or null | default=null                                           |

## `RunloopConfiguration`

| Field                     | Required | Type / choices                                   | Constraints and default                                |
| ------------------------- | -------- | ------------------------------------------------ | ------------------------------------------------------ |
| `root`                    | false    | string                                           | default="/home/user"                                   |
| `python`                  | false    | string                                           | default="python3"                                      |
| `shell`                   | false    | string                                           | default="/bin/bash"                                    |
| `read_only`               | false    | boolean                                          | default=false                                          |
| `request_timeout_seconds` | false    | number                                           | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer                                          | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer                                          | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer                                          | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `blueprint_id`            | false    | string or null                                   | default=null                                           |
| `resource_size`           | false    | "X_SMALL", "SMALL", "MEDIUM", "LARGE", "X_LARGE" | default="SMALL"                                        |
| `idle_timeout_seconds`    | false    | integer                                          | minimum=300; maximum=172800; default=3600              |

## `RemoteEnvdProviderConfiguration`

| Field              | Required | Type / choices  | Constraints and default  |
| ------------------ | -------- | --------------- | ------------------------ |
| `required_methods` | false    | array of string | maxItems=128; default=[] |

## `HostLocalProviderConfiguration`

| Field     | Required | Type / choices | Constraints and default                                |
| --------- | -------- | -------------- | ------------------------------------------------------ |
| `host_id` | false    | string         | minLength=1; maxLength=256; default from model factory |

## `DockerBackendConfiguration`

| Field         | Required | Type / choices | Constraints and default                 |
| ------------- | -------- | -------------- | --------------------------------------- |
| `docker_host` | false    | string         | minLength=1; default from model factory |

## `E2BBackendConfiguration`

| Field     | Required | Type / choices | Constraints and default                                                    |
| --------- | -------- | -------------- | -------------------------------------------------------------------------- |
| `domain`  | false    | string         | `pattern="^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$"`; default="e2b.dev" |
| `api_url` | false    | string or null | default=null                                                               |

## `E2BCredential`

| Field     | Required | Type / choices | Constraints and default        |
| --------- | -------- | -------------- | ------------------------------ |
| `api_key` | true     | string         | minLength=1; format="password" |

## `DaytonaBackendConfiguration`

| Field             | Required | Type / choices | Constraints and default                 |
| ----------------- | -------- | -------------- | --------------------------------------- |
| `organization_id` | true     | string         | minLength=1; maxLength=128              |
| `target`          | false    | string         | minLength=1; maxLength=64; default="us" |

## `ModalBackendConfiguration`

| Field              | Required | Type / choices | Constraints and default                    |
| ------------------ | -------- | -------------- | ------------------------------------------ |
| `workspace`        | true     | string         | minLength=1; maxLength=128                 |
| `app_name`         | true     | string         | minLength=1; maxLength=128                 |
| `environment_name` | false    | string         | minLength=1; maxLength=128; default="main" |

## `ModalCredential`

| Field          | Required | Type / choices | Constraints and default        |
| -------------- | -------- | -------------- | ------------------------------ |
| `token_id`     | true     | string         | minLength=1; format="password" |
| `token_secret` | true     | string         | minLength=1; format="password" |

## `VercelBackendConfiguration`

| Field        | Required | Type / choices | Constraints and default    |
| ------------ | -------- | -------------- | -------------------------- |
| `team_id`    | true     | string         | minLength=1; maxLength=128 |
| `project_id` | true     | string         | minLength=1; maxLength=128 |

## `SpritesBackendConfiguration`

| Field          | Required | Type / choices | Constraints and default    |
| -------------- | -------- | -------------- | -------------------------- |
| `organization` | true     | string         | minLength=1; maxLength=128 |

## `RunloopBackendConfiguration`

| Field          | Required | Type / choices | Constraints and default    |
| -------------- | -------- | -------------- | -------------------------- |
| `organization` | true     | string         | minLength=1; maxLength=128 |

## `TokenCredential`

| Field     | Required | Type / choices | Constraints and default        |
| --------- | -------- | -------------- | ------------------------------ |
| `api_key` | true     | string         | minLength=1; format="password" |

## `HttpEnvdBackendConfiguration`

| Field                          | Required | Type / choices | Constraints and default                      |
| ------------------------------ | -------- | -------------- | -------------------------------------------- |
| `initialization_timeout`       | false    | number         | maximum=300; exclusiveMinimum=0; default=10  |
| `request_timeout`              | false    | number         | maximum=3600; exclusiveMinimum=0; default=30 |
| `max_in_flight`                | false    | integer        | minimum=1; maximum=1024; default=32          |
| `endpoint`                     | true     | string         | minLength=1; maxLength=2048                  |
| `allow_plaintext_private_link` | false    | boolean        | default=false                                |

## `HttpEnvdCredential`

| Field   | Required | Type / choices | Constraints and default                        |
| ------- | -------- | -------------- | ---------------------------------------------- |
| `token` | true     | string         | minLength=1; maxLength=4096; format="password" |

## `WebSocketEnvdBackendConfiguration`

| Field                | Required | Type / choices | Constraints and default                     |
| -------------------- | -------- | -------------- | ------------------------------------------- |
| `connection_timeout` | false    | number         | maximum=300; exclusiveMinimum=0; default=10 |
