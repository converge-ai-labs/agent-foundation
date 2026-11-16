---
title: Provider configuration reference
description: Generated Harness execution inputs, Service management recipes, account settings, and credentials.
---

Choose the model for the boundary you are configuring. Harness execution inputs describe an already selected target. Service management recipes add target creation and desktop preparation. Account settings and credentials are separate from both. Use [Configure Providers](configuration.md) to assemble these inputs.

A field marked **Required** must be supplied. **Default from model factory** means the model computes the value when constructed. The tables show field schemas, not cross-field validation or the values selected by a Host; follow the setup guide for automatic desktop defaults and valid combinations. Standalone daemon JSON uses [Envd configuration](../a13n-envd/configuration.md), not these models.

These tables are generated from the selected built-in models in `scripts/docs/references.py`. Contributors regenerate them with `uv run --locked python scripts/docs/references.py` and synchronize the Chinese page. Keep each execution model distinct from its Service recipe.

## Cloud Providers

Choose the execution column for a fixed-target Harness connector, or the management column for a Service template. A Service recipe's automatic image or snapshot is resolved before execution. See [cloud setup and limits](cloud-providers.md) for preparation and valid desktop combinations.

| Provider       | Harness execution                                                   | Service management                                                  | Account                                                           | Credential                          |
| -------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------- |
| Docker         | [DockerEnvironmentConfiguration](#dockerenvironmentconfiguration)   | [DockerRecipe](#dockerrecipe)                                       | [DockerConnectionConfiguration](#dockerconnectionconfiguration)   | Engine authentication               |
| E2B            | [E2BEnvironmentConfiguration](#e2benvironmentconfiguration)         | [E2BRecipe](#e2brecipe)                                             | [E2BConnectionConfiguration](#e2bconnectionconfiguration)         | [E2BCredential](#e2bcredential)     |
| Daytona        | [DaytonaEnvironmentConfiguration](#daytonaenvironmentconfiguration) | [DaytonaRecipe](#daytonarecipe)                                     | [DaytonaConnectionConfiguration](#daytonaconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Modal          | [ModalEnvironmentConfiguration](#modalenvironmentconfiguration)     | [ModalRecipe](#modalrecipe)                                         | [ModalConnectionConfiguration](#modalconnectionconfiguration)     | [ModalCredential](#modalcredential) |
| Vercel Sandbox | [VercelEnvironmentConfiguration](#vercelenvironmentconfiguration)   | [VercelRecipe](#vercelrecipe)                                       | [VercelConnectionConfiguration](#vercelconnectionconfiguration)   | [TokenCredential](#tokencredential) |
| Fly.io Sprites | [SpritesEnvironmentConfiguration](#spritesenvironmentconfiguration) | [SpritesRecipe](#spritesrecipe)                                     | [SpritesConnectionConfiguration](#spritesconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Runloop        | [RunloopEnvironmentConfiguration](#runloopenvironmentconfiguration) | [RunloopEnvironmentConfiguration](#runloopenvironmentconfiguration) | [RunloopConnectionConfiguration](#runloopconnectionconfiguration) | [TokenCredential](#tokencredential) |
| boxd           | [BoxdEnvironmentConfiguration](#boxdenvironmentconfiguration)       | [BoxdEnvironmentConfiguration](#boxdenvironmentconfiguration)       | [BoxdConnectionConfiguration](#boxdconnectionconfiguration)       | [TokenCredential](#tokencredential) |

Runloop and boxd use the same configuration model for execution and management; they have no separate Service desktop recipe.

## `DirectLocalEnvironmentConfiguration`

| Field                      | Required | Type / choices                   | Constraints and default                 |
| -------------------------- | -------- | -------------------------------- | --------------------------------------- |
| `root`                     | true     | DirectLocalRootConfiguration     | —                                       |
| `shell_profiles`           | false    | array of DirectLocalShellProfile | default=[]                              |
| `allowed_executables`      | false    | array of string                  | uniqueItems=true; default=[]            |
| `environment`              | false    | EnvironmentVariables             | —; default from model factory           |
| `inherit_environment`      | false    | boolean                          | default=false                           |
| `allowed_environment_keys` | false    | array of string or null          | uniqueItems=true; default=[]            |
| `allowed_ports`            | false    | array of integer                 | uniqueItems=true; default=[]            |
| `max_value_bytes`          | false    | integer                          | exclusiveMinimum=0; default=67108864    |
| `max_concurrent_processes` | false    | integer                          | exclusiveMinimum=0; default=128         |
| `max_wall_time_seconds`    | false    | number                           | default=86400                           |
| `terminate_grace_seconds`  | false    | number                           | default=5.0                             |
| `max_buffer_bytes`         | false    | integer                          | exclusiveMinimum=0; default=1048576     |
| `max_spool_bytes`          | false    | integer                          | exclusiveMinimum=0; default=68719476736 |

## `DirectLocalRootConfiguration`

| Field  | Required | Type / choices | Constraints and default |
| ------ | -------- | -------------- | ----------------------- |
| `path` | true     | string         | format="path"           |

## `DirectLocalShellProfile`

| Field             | Required | Type / choices        | Constraints and default    |
| ----------------- | -------- | --------------------- | -------------------------- |
| `profile_id`      | true     | string                | minLength=1; maxLength=128 |
| `executable`      | true     | string                | format="path"              |
| `fixed_arguments` | false    | array of string       | default=[]                 |
| `dialect`         | false    | "posix", "powershell" | default="posix"            |
| `allow_login`     | false    | boolean               | default=false              |

## `EnvironmentVariables`

Default variables for Agent commands. Ordinary readable configuration, not secret storage. Command variables override these defaults.

Choices: `object`.

## `LocalEnvdEnvironmentConfiguration`

One fixed-cwd Session selection; never a daemon launch policy.

| Field               | Required | Type / choices                  | Constraints and default                  |
| ------------------- | -------- | ------------------------------- | ---------------------------------------- |
| `session`           | false    | EnvdSessionConfiguration        | —; default from model factory            |
| `expected_boundary` | false    | EnvdBoundaryRequirement or null | default=null                             |
| `working_directory` | false    | string or null                  | format="eip-absolute-path"; default=null |
| `required_methods`  | false    | array of string                 | maxItems=128; default=[]                 |

## `AllowlistDestinations`

| Field   | Required | Type / choices  | Constraints and default  |
| ------- | -------- | --------------- | ------------------------ |
| `mode`  | true     | "allowlist"     | —                        |
| `hosts` | false    | array of string | maxItems=256; default=[] |

## `DisabledSandbox`

| Field  | Required | Type / choices | Constraints and default |
| ------ | -------- | -------------- | ----------------------- |
| `mode` | true     | "disabled"     | —                       |

## `EgressDestinations`

Choices: `PublicDestinations or AllowlistDestinations`.

## `EgressMode`

Choices: `"inherit", "deny", "controlled"`.

## `EnvdBoundaryRequirement`

An expected effective Session boundary.

| Field                    | Required | Type / choices                  | Constraints and default |
| ------------------------ | -------- | ------------------------------- | ----------------------- |
| `sandbox`                | true     | SandboxPolicy                   | —                       |
| `egress`                 | true     | "inherit", "deny", "controlled" | —                       |
| `identity`               | false    | ExecutionIdentity or null       | default=null            |
| `privilege_gain_blocked` | false    | boolean or null                 | default=null            |

## `EnvdEgressConfiguration`

| Field          | Required | Type / choices               | Constraints and default |
| -------------- | -------- | ---------------------------- | ----------------------- |
| `destinations` | true     | EgressDestinations           | —                       |
| `secrets`      | false    | array of EnvdSecretReference | maxItems=64; default=[] |

## `EnvdSecretReference`

| Field          | Required | Type / choices              | Constraints and default                             |
| -------------- | -------- | --------------------------- | --------------------------------------------------- |
| `env`          | true     | string                      | maxLength=128; `pattern="^[A-Za-z_][A-Za-z0-9_]*$"` |
| `source`       | true     | EnvironmentCredentialSource | —                                                   |
| `inject_hosts` | true     | array of string             | minItems=1; maxItems=256                            |

## `EnvdSessionConfiguration`

Immutable Session selection; credential references are resolved at acquisition.

| Field               | Required | Type / choices                  | Constraints and default                  |
| ------------------- | -------- | ------------------------------- | ---------------------------------------- |
| `working_directory` | false    | string or null                  | format="eip-absolute-path"; default=null |
| `sandbox`           | false    | SandboxPolicy or null           | default=null                             |
| `egress_mode`       | false    | EgressMode or null              | default=null                             |
| `egress`            | false    | EnvdEgressConfiguration or null | default=null                             |
| `lifecycle`         | false    | SessionLifecyclePolicy or null  | default=null                             |
| `commands`          | false    | boolean or null                 | default=null                             |
| `isolation`         | false    | SessionIsolation or null        | default=null                             |

## `EnvironmentCredentialSource`

| Field  | Required | Type / choices | Constraints and default              |
| ------ | -------- | -------------- | ------------------------------------ |
| `kind` | true     | "environment"  | —                                    |
| `name` | true     | string         | `pattern="^[A-Za-z_][A-Za-z0-9_]*$"` |

## `ExecutionIdentity`

| Field | Required | Type / choices | Constraints and default       |
| ----- | -------- | -------------- | ----------------------------- |
| `uid` | true     | integer        | minimum=0; maximum=4294967295 |
| `gid` | true     | integer        | minimum=0; maximum=4294967295 |

## `GrantAccess`

Choices: `"read_only", "read_write"`.

## `NativeIsolation`

| Field  | Required | Type / choices | Constraints and default |
| ------ | -------- | -------------- | ----------------------- |
| `mode` | true     | "native"       | —                       |

## `PublicDestinations`

| Field  | Required | Type / choices | Constraints and default |
| ------ | -------- | -------------- | ----------------------- |
| `mode` | true     | "public"       | —                       |

## `RestrictedSandbox`

| Field            | Required | Type / choices        | Constraints and default |
| ---------------- | -------- | --------------------- | ----------------------- |
| `mode`           | true     | "restricted"          | —                       |
| `grants`         | false    | array of SandboxGrant | default=[]              |
| `full_disk_read` | false    | boolean               | default=false           |

## `SandboxGrant`

| Field    | Required | Type / choices            | Constraints and default    |
| -------- | -------- | ------------------------- | -------------------------- |
| `path`   | true     | string                    | format="eip-absolute-path" |
| `access` | true     | "read_only", "read_write" | —                          |

## `SandboxPolicy`

Choices: `DisabledSandbox or RestrictedSandbox`.

## `SessionIsolation`

Choices: `NativeIsolation or VmIsolation`.

## `SessionLifecyclePolicy`

| Field             | Required | Type / choices  | Constraints and default                               |
| ----------------- | -------- | --------------- | ----------------------------------------------------- |
| `idle_timeout_ms` | true     | integer         | minimum=1; maximum=18446744073709551615               |
| `max_lifetime_ms` | false    | integer or null | minimum=1; maximum=18446744073709551615; default=null |

## `VmBackend`

Choices: `"boxlite", "microsandbox"`.

## `VmIsolation`

| Field        | Required | Type / choices            | Constraints and default         |
| ------------ | -------- | ------------------------- | ------------------------------- |
| `mode`       | true     | "vm"                      | —                               |
| `backend`    | true     | "boxlite", "microsandbox" | —                               |
| `image`      | true     | string                    | minLength=1; maxLength=4096     |
| `cpus`       | true     | integer                   | minimum=1; maximum=255          |
| `memory_mib` | true     | integer                   | minimum=128; maximum=4294967295 |
| `mounts`     | false    | array of VmMount          | default=[]                      |

## `VmMount`

| Field    | Required | Type / choices            | Constraints and default    |
| -------- | -------- | ------------------------- | -------------------------- |
| `source` | true     | string                    | format="eip-absolute-path" |
| `target` | true     | string                    | format="eip-absolute-path" |
| `access` | true     | "read_only", "read_write" | —                          |

## `LocalEnvdLaunchConfiguration`

Host-selected daemon launch configuration, shared by every Session on this runtime.

| Field                                | Required | Type / choices                           | Constraints and default                             |
| ------------------------------------ | -------- | ---------------------------------------- | --------------------------------------------------- |
| `execution`                          | false    | EnvdExecutionConfiguration               | —; default from model factory                       |
| `sandbox`                            | false    | SandboxPolicy                            | —; default from model factory                       |
| `egress`                             | false    | EnvdNetworkConfiguration                 | —; default from model factory                       |
| `default_working_directory`          | false    | string or null                           | format="path"; default=null                         |
| `directory_discovery`                | false    | boolean                                  | default=true                                        |
| `full_control`                       | false    | boolean                                  | default=false                                       |
| `computer_use`                       | false    | boolean                                  | default=false                                       |
| `computer_use_permission_timeout_ms` | false    | integer                                  | exclusiveMinimum=0; default=120000                  |
| `monitoring`                         | false    | LocalEnvdMonitoringConfiguration or null | default=null                                        |
| `trusted_executable_roots`           | false    | array of string                          | default=[]                                          |
| `shell_profiles`                     | false    | array of LocalEnvdShellProfile           | default=[]                                          |
| `max_file_bytes`                     | false    | integer                                  | exclusiveMinimum=0; default=16777216                |
| `max_output_preview_bytes`           | false    | integer                                  | maximum=16777216; exclusiveMinimum=0; default=65536 |
| `max_output_bytes_per_stream`        | false    | integer                                  | exclusiveMinimum=0; default=268435456               |
| `max_spool_bytes`                    | false    | integer                                  | exclusiveMinimum=0; default=1073741824              |
| `max_device_spool_bytes`             | false    | integer                                  | exclusiveMinimum=0; default=4294967296              |

## `EnvdExecutionConfiguration`

| Field        | Required | Type / choices  | Constraints and default                              |
| ------------ | -------- | --------------- | ---------------------------------------------------- |
| `uid`        | false    | integer or null | minimum=0; exclusiveMaximum=4294967295; default=null |
| `gid`        | false    | integer or null | minimum=0; exclusiveMaximum=4294967295; default=null |
| `allow_sudo` | false    | boolean         | default=true                                         |

## `EnvdNetworkConfiguration`

| Field  | Required | Type / choices                  | Constraints and default |
| ------ | -------- | ------------------------------- | ----------------------- |
| `mode` | true     | "inherit", "deny", "controlled" | —                       |

## `LocalEnvdMonitoringConfiguration`

Optional outer-daemon sampling; independent of Session execution policy.

| Field                 | Required | Type / choices | Constraints and default                    |
| --------------------- | -------- | -------------- | ------------------------------------------ |
| `sample_interval_ms`  | false    | integer        | minimum=1000; maximum=60000; default=10000 |
| `filesystems`         | false    | object         | —; default from model factory              |
| `cgroup_v2_directory` | false    | string or null | format="path"; default=null                |

## `LocalEnvdShellProfile`

| Field              | Required | Type / choices  | Constraints and default             |
| ------------------ | -------- | --------------- | ----------------------------------- |
| `profile_id`       | true     | string          | minLength=1; maxLength=128          |
| `executable`       | true     | string          | format="path"                       |
| `fixed_arguments`  | false    | array of string | default=[]                          |
| `allow_login`      | false    | boolean         | default=false                       |
| `max_script_bytes` | false    | integer         | exclusiveMinimum=0; default=1048576 |

## `DockerEnvironmentConfiguration`

| Field                         | Required | Type / choices                       | Constraints and default                                                          |
| ----------------------------- | -------- | ------------------------------------ | -------------------------------------------------------------------------------- |
| `image`                       | false    | string                               | minLength=1; maxLength=1024; default="ghcr.io/converge-ai-labs/a13n-sandbox:dev" |
| `execution`                   | false    | "native" or "envd_http" or "envd_ws" | default="native"                                                                 |
| `desktop`                     | false    | boolean                              | default=false                                                                    |
| `mounts`                      | false    | array of DockerMountConfiguration    | default=[]                                                                       |
| `environment`                 | false    | EnvironmentVariables                 | —; default from model factory                                                    |
| `init_script`                 | false    | string or null                       | maxLength=1048576; format="multiline"; default=null                              |
| `disable_network`             | false    | boolean                              | default=false                                                                    |
| `user`                        | false    | string or null                       | minLength=1; maxLength=128; default=null                                         |
| `shell`                       | false    | string                               | default="/bin/sh"                                                                |
| `python`                      | false    | string                               | default="python3"                                                                |
| `cpus`                        | false    | number or null                       | minimum=0.001; default=null                                                      |
| `memory_gb`                   | false    | number or null                       | minimum=0.006291456; default=null                                                |
| `pids_limit`                  | false    | integer or null                      | exclusiveMinimum=0; default=null                                                 |
| `stop_grace_seconds`          | false    | integer                              | minimum=0; maximum=300; default=10                                               |
| `request_timeout_seconds`     | false    | integer                              | maximum=3600; exclusiveMinimum=0; default=60                                     |
| `max_file_bytes`              | false    | integer                              | exclusiveMinimum=0; default=16777216                                             |
| `max_query_entries`           | false    | integer                              | exclusiveMinimum=0; default=100000                                               |
| `max_output_preview_bytes`    | false    | integer                              | exclusiveMinimum=0; default=65536                                                |
| `max_output_bytes_per_stream` | false    | integer                              | exclusiveMinimum=0; default=16777216                                             |
| `max_spool_bytes`             | false    | integer                              | exclusiveMinimum=0; default=67108864                                             |
| `max_concurrent_processes`    | false    | integer                              | exclusiveMinimum=0; default=128                                                  |

## `DockerMountConfiguration`

| Field       | Required | Type / choices | Constraints and default |
| ----------- | -------- | -------------- | ----------------------- |
| `source`    | true     | string         | —                       |
| `target`    | true     | string         | format="path"           |
| `read_only` | false    | boolean        | default=true            |

## `DockerRecipe`

| Field                         | Required | Type / choices                       | Constraints and default                                                          |
| ----------------------------- | -------- | ------------------------------------ | -------------------------------------------------------------------------------- |
| `envd_management`             | false    | "managed", "preinstalled"            | default="preinstalled"                                                           |
| `image`                       | false    | string                               | minLength=1; maxLength=1024; default="ghcr.io/converge-ai-labs/a13n-sandbox:dev" |
| `execution`                   | false    | "native" or "envd_http" or "envd_ws" | default="native"                                                                 |
| `desktop`                     | false    | boolean                              | default=false                                                                    |
| `mounts`                      | false    | array of DockerMountConfiguration    | default=[]                                                                       |
| `environment`                 | false    | EnvironmentVariables                 | —; default from model factory                                                    |
| `init_script`                 | false    | string or null                       | maxLength=1048576; format="multiline"; default=null                              |
| `disable_network`             | false    | boolean                              | default=false                                                                    |
| `user`                        | false    | string or null                       | minLength=1; maxLength=128; default=null                                         |
| `shell`                       | false    | string                               | default="/bin/sh"                                                                |
| `python`                      | false    | string                               | default="python3"                                                                |
| `cpus`                        | false    | number or null                       | minimum=0.001; default=null                                                      |
| `memory_gb`                   | false    | number or null                       | minimum=0.006291456; default=null                                                |
| `pids_limit`                  | false    | integer or null                      | exclusiveMinimum=0; default=null                                                 |
| `stop_grace_seconds`          | false    | integer                              | minimum=0; maximum=300; default=10                                               |
| `request_timeout_seconds`     | false    | integer                              | maximum=3600; exclusiveMinimum=0; default=60                                     |
| `max_file_bytes`              | false    | integer                              | exclusiveMinimum=0; default=16777216                                             |
| `max_query_entries`           | false    | integer                              | exclusiveMinimum=0; default=100000                                               |
| `max_output_preview_bytes`    | false    | integer                              | exclusiveMinimum=0; default=65536                                                |
| `max_output_bytes_per_stream` | false    | integer                              | exclusiveMinimum=0; default=16777216                                             |
| `max_spool_bytes`             | false    | integer                              | exclusiveMinimum=0; default=67108864                                             |
| `max_concurrent_processes`    | false    | integer                              | exclusiveMinimum=0; default=128                                                  |
| `desktop_assistance`          | false    | boolean                              | default=false                                                                    |
| `desktop_viewer_url`          | false    | string or null                       | maxLength=2048; default=null                                                     |
| `pull_policy`                 | false    | "never", "if_missing"                | default="if_missing"                                                             |

## `E2BEnvironmentConfiguration`

| Field                       | Required | Type / choices                   | Constraints and default                                   |
| --------------------------- | -------- | -------------------------------- | --------------------------------------------------------- |
| `template`                  | false    | string                           | minLength=1; maxLength=256; default="base"                |
| `execution`                 | false    | "native", "envd_http", "envd_ws" | default="native"                                          |
| `preparation`               | false    | "preinstalled", "headless"       | default="preinstalled"                                    |
| `desktop`                   | false    | boolean                          | default=false                                             |
| `environment`               | false    | EnvironmentVariables             | —; default from model factory                             |
| `root`                      | false    | string                           | default="/home/user"                                      |
| `user`                      | false    | string                           | `pattern="^[a-z_][a-z0-9_-]{0,63}$"`; default="user"      |
| `python`                    | false    | string                           | default="/usr/bin/python3"                                |
| `request_timeout_seconds`   | false    | number                           | maximum=300; exclusiveMinimum=0; default=30               |
| `allow_internet_access`     | false    | boolean                          | default=true                                              |
| `max_file_bytes`            | false    | integer                          | maximum=1073741824; exclusiveMinimum=0; default=104857600 |
| `max_observation_bytes`     | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576     |
| `max_active_observations`   | false    | integer                          | maximum=1024; exclusiveMinimum=0; default=128             |
| `max_retained_output_bytes` | false    | integer                          | maximum=1073741824; exclusiveMinimum=0; default=134217728 |
| `max_query_entries`         | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000         |

## `E2BRecipe`

| Field                       | Required | Type / choices                   | Constraints and default                                   |
| --------------------------- | -------- | -------------------------------- | --------------------------------------------------------- |
| `envd_management`           | false    | "managed", "preinstalled"        | default="preinstalled"                                    |
| `template`                  | false    | string                           | minLength=1; maxLength=256; default="base"                |
| `execution`                 | false    | "native", "envd_http", "envd_ws" | default="native"                                          |
| `preparation`               | false    | "preinstalled", "headless"       | default="preinstalled"                                    |
| `desktop`                   | false    | boolean                          | default=false                                             |
| `environment`               | false    | EnvironmentVariables             | —; default from model factory                             |
| `root`                      | false    | string                           | default="/home/user"                                      |
| `user`                      | false    | string                           | `pattern="^[a-z_][a-z0-9_-]{0,63}$"`; default="user"      |
| `python`                    | false    | string                           | default="/usr/bin/python3"                                |
| `request_timeout_seconds`   | false    | number                           | maximum=300; exclusiveMinimum=0; default=30               |
| `allow_internet_access`     | false    | boolean                          | default=true                                              |
| `max_file_bytes`            | false    | integer                          | maximum=1073741824; exclusiveMinimum=0; default=104857600 |
| `max_observation_bytes`     | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576     |
| `max_active_observations`   | false    | integer                          | maximum=1024; exclusiveMinimum=0; default=128             |
| `max_retained_output_bytes` | false    | integer                          | maximum=1073741824; exclusiveMinimum=0; default=134217728 |
| `max_query_entries`         | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000         |
| `desktop_assistance`        | false    | boolean                          | default=false                                             |
| `timeout_seconds`           | false    | integer                          | minimum=30; maximum=86400; default=3600                   |

## `DaytonaRecipe`

| Field                     | Required | Type / choices            | Constraints and default                                                                     |
| ------------------------- | -------- | ------------------------- | ------------------------------------------------------------------------------------------- |
| `envd_management`         | false    | "managed", "preinstalled" | default="preinstalled"                                                                      |
| `snapshot`                | false    | string                    | minLength=1; maxLength=256; default="auto"                                                  |
| `desktop`                 | false    | boolean                   | default=false                                                                               |
| `execution`               | false    | "envd_http", "envd_ws"    | default="envd_http"                                                                         |
| `environment`             | false    | EnvironmentVariables      | —; default from model factory                                                               |
| `request_timeout_seconds` | false    | number                    | maximum=600; exclusiveMinimum=0; default=120                                                |
| `base_image`              | false    | string or null            | maxLength=512; `pattern="^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}$"`; default=null |
| `desktop_assistance`      | false    | boolean                   | default=false                                                                               |

## `ModalRecipe`

| Field                     | Required | Type / choices            | Constraints and default                                                                     |
| ------------------------- | -------- | ------------------------- | ------------------------------------------------------------------------------------------- |
| `envd_management`         | false    | "managed", "preinstalled" | default="preinstalled"                                                                      |
| `image`                   | false    | string                    | minLength=1; maxLength=256; default="auto"                                                  |
| `desktop`                 | false    | boolean                   | default=false                                                                               |
| `execution`               | false    | "envd_http", "envd_ws"    | default="envd_http"                                                                         |
| `environment`             | false    | EnvironmentVariables      | —; default from model factory                                                               |
| `request_timeout_seconds` | false    | number                    | maximum=600; exclusiveMinimum=0; default=120                                                |
| `timeout_seconds`         | false    | integer                   | minimum=300; maximum=86400; default=86400                                                   |
| `cpu`                     | false    | number                    | maximum=64; exclusiveMinimum=0; default=1                                                   |
| `memory`                  | false    | integer                   | minimum=128; maximum=262144; default=1024                                                   |
| `startup_command`         | false    | array of string or null   | minItems=1; default=null                                                                    |
| `base_image`              | false    | string or null            | maxLength=512; `pattern="^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}$"`; default=null |
| `desktop_assistance`      | false    | boolean                   | default=false                                                                               |

## `VercelRecipe`

| Field                     | Required | Type / choices                   | Constraints and default                                                             |
| ------------------------- | -------- | -------------------------------- | ----------------------------------------------------------------------------------- |
| `envd_management`         | false    | "managed", "preinstalled"        | default="preinstalled"                                                              |
| `environment`             | false    | EnvironmentVariables             | —; default from model factory                                                       |
| `root`                    | false    | string                           | default="/vercel/sandbox"                                                           |
| `python`                  | false    | string                           | default="/vercel/runtimes/python/bin/python3"                                       |
| `shell`                   | false    | string                           | default="/bin/bash"                                                                 |
| `request_timeout_seconds` | false    | number                           | maximum=600; exclusiveMinimum=0; default=120                                        |
| `max_file_bytes`          | false    | integer                          | maximum=67108864; exclusiveMinimum=0; default=16777216                              |
| `max_query_entries`       | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000                                   |
| `max_output_bytes`        | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576                               |
| `execution`               | false    | "native", "envd_http", "envd_ws" | default="native"                                                                    |
| `snapshot`                | false    | string or null                   | minLength=1; maxLength=256; `pattern="^(auto\|snap_[A-Za-z0-9_-]+)$"`; default=null |
| `desktop`                 | false    | boolean                          | default=false                                                                       |
| `runtime`                 | false    | string                           | minLength=1; maxLength=128; default="python3.13"                                    |
| `timeout_seconds`         | false    | integer                          | minimum=300; maximum=18000; default=3600                                            |
| `vcpus`                   | false    | integer                          | minimum=2; maximum=8; default=2                                                     |
| `startup_command`         | false    | array of string or null          | minItems=1; default=null                                                            |
| `desktop_assistance`      | false    | boolean                          | default=false                                                                       |

## `SpritesRecipe`

| Field                     | Required | Type / choices                   | Constraints and default                                |
| ------------------------- | -------- | -------------------------------- | ------------------------------------------------------ |
| `envd_management`         | false    | "managed", "preinstalled"        | default="preinstalled"                                 |
| `environment`             | false    | EnvironmentVariables             | —; default from model factory                          |
| `root`                    | false    | string                           | default="/home/sprite"                                 |
| `python`                  | false    | string                           | default="python3"                                      |
| `shell`                   | false    | string                           | default="/bin/bash"                                    |
| `request_timeout_seconds` | false    | number                           | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer                          | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `region`                  | false    | string or null                   | maxLength=64; default=null                             |
| `execution`               | false    | "native", "envd_http", "envd_ws" | default="native"                                       |
| `desktop`                 | false    | boolean                          | default=false                                          |
| `desktop_assistance`      | false    | boolean                          | default=false                                          |

## `DaytonaEnvironmentConfiguration`

| Field                     | Required | Type / choices         | Constraints and default                      |
| ------------------------- | -------- | ---------------------- | -------------------------------------------- |
| `snapshot`                | true     | string                 | minLength=1; maxLength=256                   |
| `desktop`                 | false    | boolean                | default=false                                |
| `execution`               | false    | "envd_http", "envd_ws" | default="envd_http"                          |
| `environment`             | false    | EnvironmentVariables   | —; default from model factory                |
| `request_timeout_seconds` | false    | number                 | maximum=600; exclusiveMinimum=0; default=120 |

## `ModalEnvironmentConfiguration`

| Field                     | Required | Type / choices         | Constraints and default                      |
| ------------------------- | -------- | ---------------------- | -------------------------------------------- |
| `image`                   | true     | string                 | minLength=1; maxLength=256                   |
| `desktop`                 | false    | boolean                | default=false                                |
| `execution`               | false    | "envd_http", "envd_ws" | default="envd_http"                          |
| `environment`             | false    | EnvironmentVariables   | —; default from model factory                |
| `request_timeout_seconds` | false    | number                 | maximum=600; exclusiveMinimum=0; default=120 |
| `timeout_seconds`         | false    | integer                | minimum=300; maximum=86400; default=86400    |
| `cpu`                     | false    | number                 | maximum=64; exclusiveMinimum=0; default=1    |
| `memory`                  | false    | integer                | minimum=128; maximum=262144; default=1024    |

## `VercelEnvironmentConfiguration`

| Field                     | Required | Type / choices                   | Constraints and default                                                             |
| ------------------------- | -------- | -------------------------------- | ----------------------------------------------------------------------------------- |
| `environment`             | false    | EnvironmentVariables             | —; default from model factory                                                       |
| `root`                    | false    | string                           | default="/vercel/sandbox"                                                           |
| `python`                  | false    | string                           | default="/vercel/runtimes/python/bin/python3"                                       |
| `shell`                   | false    | string                           | default="/bin/bash"                                                                 |
| `request_timeout_seconds` | false    | number                           | maximum=600; exclusiveMinimum=0; default=120                                        |
| `max_file_bytes`          | false    | integer                          | maximum=67108864; exclusiveMinimum=0; default=16777216                              |
| `max_query_entries`       | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000                                   |
| `max_output_bytes`        | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576                               |
| `execution`               | false    | "native", "envd_http", "envd_ws" | default="native"                                                                    |
| `snapshot`                | false    | string or null                   | minLength=1; maxLength=256; `pattern="^(auto\|snap_[A-Za-z0-9_-]+)$"`; default=null |
| `desktop`                 | false    | boolean                          | default=false                                                                       |
| `runtime`                 | false    | string                           | minLength=1; maxLength=128; default="python3.13"                                    |
| `timeout_seconds`         | false    | integer                          | minimum=300; maximum=18000; default=3600                                            |
| `vcpus`                   | false    | integer                          | minimum=2; maximum=8; default=2                                                     |

## `SpritesEnvironmentConfiguration`

| Field                     | Required | Type / choices                   | Constraints and default                                |
| ------------------------- | -------- | -------------------------------- | ------------------------------------------------------ |
| `environment`             | false    | EnvironmentVariables             | —; default from model factory                          |
| `root`                    | false    | string                           | default="/home/sprite"                                 |
| `python`                  | false    | string                           | default="python3"                                      |
| `shell`                   | false    | string                           | default="/bin/bash"                                    |
| `request_timeout_seconds` | false    | number                           | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer                          | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer                          | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer                          | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `region`                  | false    | string or null                   | maxLength=64; default=null                             |
| `execution`               | false    | "native", "envd_http", "envd_ws" | default="native"                                       |
| `desktop`                 | false    | boolean                          | default=false                                          |

## `RunloopEnvironmentConfiguration`

| Field                     | Required | Type / choices                                   | Constraints and default                                |
| ------------------------- | -------- | ------------------------------------------------ | ------------------------------------------------------ |
| `environment`             | false    | EnvironmentVariables                             | —; default from model factory                          |
| `root`                    | false    | string                                           | default="/home/user"                                   |
| `python`                  | false    | string                                           | default="python3"                                      |
| `shell`                   | false    | string                                           | default="/bin/bash"                                    |
| `request_timeout_seconds` | false    | number                                           | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false    | integer                                          | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false    | integer                                          | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false    | integer                                          | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `blueprint_id`            | false    | string or null                                   | maxLength=128; default=null                            |
| `resource_size`           | false    | "X_SMALL", "SMALL", "MEDIUM", "LARGE", "X_LARGE" | default="SMALL"                                        |
| `idle_timeout_seconds`    | false    | integer                                          | minimum=300; maximum=172800; default=3600              |

## `BoxdEnvironmentConfiguration`

| Field                            | Required | Type / choices                       | Constraints and default                                |
| -------------------------------- | -------- | ------------------------------------ | ------------------------------------------------------ |
| `environment`                    | false    | EnvironmentVariables                 | —; default from model factory                          |
| `root`                           | false    | string                               | default="/home/boxd"                                   |
| `python`                         | false    | string                               | default="python3"                                      |
| `shell`                          | false    | string                               | default="/bin/bash"                                    |
| `request_timeout_seconds`        | false    | number                               | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`                 | false    | integer                              | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`              | false    | integer                              | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`               | false    | integer                              | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `size`                           | false    | "1x4", "2x8", "4x16", "8x32" or null | default=null                                           |
| `from_snapshot`                  | false    | string or null                       | minLength=1; maxLength=128; default=null               |
| `auto_suspend_timeout_seconds`   | false    | integer                              | minimum=0; maximum=86400; default=0                    |
| `auto_hibernate_timeout_seconds` | false    | integer                              | minimum=0; maximum=604800; default=0                   |

## `RemoteEnvdEnvironmentConfiguration`

| Field               | Required | Type / choices                  | Constraints and default                  |
| ------------------- | -------- | ------------------------------- | ---------------------------------------- |
| `session`           | false    | EnvdSessionConfiguration        | —; default from model factory            |
| `expected_boundary` | false    | EnvdBoundaryRequirement or null | default=null                             |
| `working_directory` | false    | string or null                  | format="eip-absolute-path"; default=null |
| `required_methods`  | false    | array of string                 | maxItems=128; default=[]                 |

## `HostLocalProviderConfiguration`

| Field     | Required | Type / choices | Constraints and default                                |
| --------- | -------- | -------------- | ------------------------------------------------------ |
| `host_id` | false    | string         | minLength=1; maxLength=256; default from model factory |

## `DockerConnectionConfiguration`

| Field         | Required | Type / choices                  | Constraints and default                 |
| ------------- | -------- | ------------------------------- | --------------------------------------- |
| `envd_http`   | false    | DockerHttpConfiguration or null | default=null                            |
| `docker_host` | false    | string                          | minLength=1; default from model factory |

## `DockerHttpConfiguration`

| Field                          | Required | Type / choices | Constraints and default                                 |
| ------------------------------ | -------- | -------------- | ------------------------------------------------------- |
| `host`                         | true     | string         | minLength=1; maxLength=253                              |
| `bind_host`                    | false    | string or null | default=null                                            |
| `allow_plaintext_private_link` | false    | boolean        | default=false                                           |
| `credential_file`              | false    | string         | maxLength=4096; default="/var/lib/a13n-envd/http-token" |

## `E2BConnectionConfiguration`

| Field     | Required | Type / choices | Constraints and default                                                    |
| --------- | -------- | -------------- | -------------------------------------------------------------------------- |
| `domain`  | false    | string         | `pattern="^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$"`; default="e2b.dev" |
| `api_url` | false    | string or null | default=null                                                               |

## `E2BCredential`

| Field     | Required | Type / choices | Constraints and default        |
| --------- | -------- | -------------- | ------------------------------ |
| `api_key` | true     | string         | minLength=1; format="password" |

## `DaytonaConnectionConfiguration`

| Field             | Required | Type / choices | Constraints and default                  |
| ----------------- | -------- | -------------- | ---------------------------------------- |
| `organization_id` | false    | string or null | minLength=1; maxLength=128; default=null |
| `target`          | false    | string         | minLength=1; maxLength=64; default="us"  |

## `ModalConnectionConfiguration`

| Field              | Required | Type / choices | Constraints and default                              |
| ------------------ | -------- | -------------- | ---------------------------------------------------- |
| `workspace`        | false    | string or null | minLength=1; maxLength=128; default=null             |
| `app_name`         | false    | string         | minLength=1; maxLength=128; default="a13n-sandboxes" |
| `environment_name` | false    | string         | minLength=1; maxLength=128; default="main"           |

## `ModalCredential`

| Field          | Required | Type / choices | Constraints and default        |
| -------------- | -------- | -------------- | ------------------------------ |
| `token_id`     | true     | string         | minLength=1; format="password" |
| `token_secret` | true     | string         | minLength=1; format="password" |

## `VercelConnectionConfiguration`

| Field        | Required | Type / choices | Constraints and default    |
| ------------ | -------- | -------------- | -------------------------- |
| `team_id`    | true     | string         | minLength=1; maxLength=128 |
| `project_id` | true     | string         | minLength=1; maxLength=128 |

## `SpritesConnectionConfiguration`

| Field          | Required | Type / choices | Constraints and default    |
| -------------- | -------- | -------------- | -------------------------- |
| `organization` | true     | string         | minLength=1; maxLength=128 |

## `RunloopConnectionConfiguration`

| Field          | Required | Type / choices | Constraints and default    |
| -------------- | -------- | -------------- | -------------------------- |
| `organization` | true     | string         | minLength=1; maxLength=128 |

## `BoxdConnectionConfiguration`

| Field          | Required | Type / choices | Constraints and default                  |
| -------------- | -------- | -------------- | ---------------------------------------- |
| `region`       | true     | "fra", "nyc"   | —                                        |
| `organization` | false    | string or null | minLength=1; maxLength=128; default=null |

## `TokenCredential`

| Field     | Required | Type / choices | Constraints and default        |
| --------- | -------- | -------------- | ------------------------------ |
| `api_key` | true     | string         | minLength=1; format="password" |

## `HttpEnvdConnectionConfiguration`

| Field                          | Required | Type / choices | Constraints and default                      |
| ------------------------------ | -------- | -------------- | -------------------------------------------- |
| `initialization_timeout`       | false    | number         | maximum=300; exclusiveMinimum=0; default=10  |
| `request_timeout`              | false    | number         | maximum=3600; exclusiveMinimum=0; default=30 |
| `max_in_flight`                | false    | integer        | minimum=1; maximum=1024; default=32          |
| `endpoint`                     | true     | string         | minLength=1; maxLength=2048                  |
| `allow_plaintext_private_link` | false    | boolean        | default=false                                |

## `HttpEnvdCredential`

| Field     | Required | Type / choices | Constraints and default                        |
| --------- | -------- | -------------- | ---------------------------------------------- |
| `headers` | false    | object         | —; default from model factory                  |
| `token`   | true     | string         | minLength=1; maxLength=4096; format="password" |

## `WebSocketEnvdConnectionConfiguration`

| Field                | Required | Type / choices | Constraints and default                     |
| -------------------- | -------- | -------------- | ------------------------------------------- |
| `connection_timeout` | false    | number         | maximum=300; exclusiveMinimum=0; default=10 |
