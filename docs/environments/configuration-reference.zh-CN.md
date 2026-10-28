---
title: Provider 配置参考
description: 从 provider 模型生成的全部内置环境 provider 配置字段。
---

找到所用 Provider 的配置模型，再查表确认字段名、可选值和默认值。`DirectLocalRootConfiguration` 等类型在下方有单独的表格。要把目标配置、后端设置和私有凭据组合成可用配置，先读[配置 Provider](configuration.md)。

标为**必填**的字段必须提供。**默认值由模型工厂计算**表示模型在构造时计算该值；本参考页不会解析你的 Host 环境或凭据存储中的值。表格描述 Provider 配置对象。独立守护进程的 JSON 设置见 [Envd 配置](../a13n-envd/configuration.md)。

表格由内置 Provider 模型生成。贡献者修改模型后，用 `uv run --locked python scripts/docs/references.py` 重新生成英文页面，并同步翻译后的表格行。

## 云 provider

通过下表链接查找各云 Provider 的三类配置对象。目标配置描述要创建的目标，后端设置选择账号或端点，凭据提供私有认证信息。支持的操作和配置示例见[云 Provider](providers.md#cloud-providers)。

| Provider       | 目标配置                                                            | 后端                                                              | 凭据                                |
| -------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------- |
| E2B            | [E2BRecipe](#e2brecipe)                                             | [E2BConnectionConfiguration](#e2bconnectionconfiguration)         | [E2BCredential](#e2bcredential)     |
| Daytona        | [DaytonaEnvironmentConfiguration](#daytonaenvironmentconfiguration) | [DaytonaConnectionConfiguration](#daytonaconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Modal          | [ModalEnvironmentConfiguration](#modalenvironmentconfiguration)     | [ModalConnectionConfiguration](#modalconnectionconfiguration)     | [ModalCredential](#modalcredential) |
| Vercel Sandbox | [VercelEnvironmentConfiguration](#vercelenvironmentconfiguration)   | [VercelConnectionConfiguration](#vercelconnectionconfiguration)   | [TokenCredential](#tokencredential) |
| Fly.io Sprites | [SpritesEnvironmentConfiguration](#spritesenvironmentconfiguration) | [SpritesConnectionConfiguration](#spritesconnectionconfiguration) | [TokenCredential](#tokencredential) |
| Runloop        | [RunloopEnvironmentConfiguration](#runloopenvironmentconfiguration) | [RunloopConnectionConfiguration](#runloopconnectionconfiguration) | [TokenCredential](#tokencredential) |

## `DirectLocalEnvironmentConfiguration`

| 字段                       | 必填  | 类型 / 可选值                            | 约束与默认值                            |
| -------------------------- | ----- | ---------------------------------------- | --------------------------------------- |
| `root`                     | true  | DirectLocalRootConfiguration             | —                                       |
| `shell_profiles`           | false | 数组，元素类型为 DirectLocalShellProfile | default=[]                              |
| `allowed_executables`      | false | string 数组                              | uniqueItems=true; default=[]            |
| `environment`              | false | EnvironmentVariables                     | —; 默认值由模型工厂计算                 |
| `inherit_environment`      | false | boolean                                  | default=false                           |
| `allowed_environment_keys` | false | string 数组 或 null                      | uniqueItems=true; default=[]            |
| `allowed_ports`            | false | integer 数组                             | uniqueItems=true; default=[]            |
| `max_value_bytes`          | false | integer                                  | exclusiveMinimum=0; default=67108864    |
| `max_concurrent_processes` | false | integer                                  | exclusiveMinimum=0; default=128         |
| `max_wall_time_seconds`    | false | number                                   | default=86400                           |
| `terminate_grace_seconds`  | false | number                                   | default=5.0                             |
| `max_buffer_bytes`         | false | integer                                  | exclusiveMinimum=0; default=1048576     |
| `max_spool_bytes`          | false | integer                                  | exclusiveMinimum=0; default=68719476736 |

## `DirectLocalRootConfiguration`

| 字段   | 必填 | 类型 / 可选值 | 约束与默认值  |
| ------ | ---- | ------------- | ------------- |
| `path` | true | string        | format="path" |

## `DirectLocalShellProfile`

| 字段              | 必填  | 类型 / 可选值         | 约束与默认值               |
| ----------------- | ----- | --------------------- | -------------------------- |
| `profile_id`      | true  | string                | minLength=1; maxLength=128 |
| `executable`      | true  | string                | format="path"              |
| `fixed_arguments` | false | string 数组           | default=[]                 |
| `dialect`         | false | "posix", "powershell" | default="posix"            |
| `allow_login`     | false | boolean               | default=false              |

## `EnvironmentVariables`

Agent 命令的默认变量。它们是可读取的普通配置，不用于存放密钥；命令显式指定的值优先。

类型：`object`，键和值均为字符串。

## `LocalEnvdEnvironmentConfiguration`

选择一个固定 cwd 的会话，不设置守护进程启动策略。

| 字段                | 必填  | 类型 / 可选值                   | 约束与默认值                             |
| ------------------- | ----- | ------------------------------- | ---------------------------------------- |
| `egress`            | false | EnvdEgressConfiguration 或 null | default=null                             |
| `expected_boundary` | false | EnvdBoundaryRequirement 或 null | default=null                             |
| `working_directory` | false | string 或 null                  | format="eip-absolute-path"; default=null |
| `required_methods`  | false | string 数组                     | maxItems=128; default=[]                 |

## `AllowlistDestinations`

| 字段    | 必填  | 类型 / 可选值 | 约束与默认值             |
| ------- | ----- | ------------- | ------------------------ |
| `mode`  | true  | "allowlist"   | —                        |
| `hosts` | false | string 数组   | maxItems=256; default=[] |

## `DisabledSandbox`

| 字段   | 必填 | 类型 / 可选值 | 约束与默认值 |
| ------ | ---- | ------------- | ------------ |
| `mode` | true | "disabled"    | —            |

## `EgressDestinations`

可选值： `PublicDestinations or AllowlistDestinations`.

## `EgressMode`

可选值： `"inherit", "deny", "controlled"`.

## `EnvdBoundaryRequirement`

选择远程边界，不授予修改该设备的权限。

| 字段                     | 必填  | 类型 / 可选值                   | 约束与默认值 |
| ------------------------ | ----- | ------------------------------- | ------------ |
| `sandbox`                | true  | SandboxPolicy                   | —            |
| `egress`                 | true  | "inherit", "deny", "controlled" | —            |
| `identity`               | false | ExecutionIdentity 或 null       | default=null |
| `privilege_gain_blocked` | false | boolean 或 null                 | default=null |

## `EnvdEgressConfiguration`

| 字段           | 必填  | 类型 / 可选值                        | 约束与默认值            |
| -------------- | ----- | ------------------------------------ | ----------------------- |
| `destinations` | true  | EgressDestinations                   | —                       |
| `secrets`      | false | 数组，元素类型为 EnvdSecretReference | maxItems=64; default=[] |

## `EnvdSecretReference`

| 字段           | 必填 | 类型 / 可选值               | 约束与默认值                                        |
| -------------- | ---- | --------------------------- | --------------------------------------------------- |
| `env`          | true | string                      | maxLength=128; `pattern="^[A-Za-z_][A-Za-z0-9_]*$"` |
| `source`       | true | EnvironmentCredentialSource | —                                                   |
| `inject_hosts` | true | string 数组                 | minItems=1; maxItems=256                            |

## `EnvironmentCredentialSource`

| 字段   | 必填 | 类型 / 可选值 | 约束与默认值                         |
| ------ | ---- | ------------- | ------------------------------------ |
| `kind` | true | "environment" | —                                    |
| `name` | true | string        | `pattern="^[A-Za-z_][A-Za-z0-9_]*$"` |

## `ExecutionIdentity`

| 字段  | 必填 | 类型 / 可选值 | 约束与默认值                  |
| ----- | ---- | ------------- | ----------------------------- |
| `uid` | true | integer       | minimum=0; maximum=4294967295 |
| `gid` | true | integer       | minimum=0; maximum=4294967295 |

## `GrantAccess`

可选值： `"read_only", "read_write"`.

## `PublicDestinations`

| 字段   | 必填 | 类型 / 可选值 | 约束与默认值 |
| ------ | ---- | ------------- | ------------ |
| `mode` | true | "public"      | —            |

## `RestrictedSandbox`

| 字段             | 必填  | 类型 / 可选值                 | 约束与默认值  |
| ---------------- | ----- | ----------------------------- | ------------- |
| `mode`           | true  | "restricted"                  | —             |
| `grants`         | false | 数组，元素类型为 SandboxGrant | default=[]    |
| `full_disk_read` | false | boolean                       | default=false |

## `SandboxGrant`

| 字段     | 必填 | 类型 / 可选值             | 约束与默认值               |
| -------- | ---- | ------------------------- | -------------------------- |
| `path`   | true | string                    | format="eip-absolute-path" |
| `access` | true | "read_only", "read_write" | —                          |

## `SandboxPolicy`

可选值： `DisabledSandbox or RestrictedSandbox`.

## `LocalEnvdLaunchConfiguration`

由 Host 选择的守护进程启动配置，由该运行时上的所有会话共享。

| 字段                                 | 必填  | 类型 / 可选值                            | 约束与默认值                                        |
| ------------------------------------ | ----- | ---------------------------------------- | --------------------------------------------------- |
| `execution`                          | false | EnvdExecutionConfiguration               | —; 默认值由模型工厂计算                             |
| `sandbox`                            | false | SandboxPolicy                            | —; 默认值由模型工厂计算                             |
| `egress`                             | false | EnvdNetworkConfiguration                 | —; 默认值由模型工厂计算                             |
| `default_working_directory`          | false | string 或 null                           | format="path"; default=null                         |
| `directory_discovery`                | false | boolean                                  | default=true                                        |
| `full_control`                       | false | boolean                                  | default=false                                       |
| `computer_use`                       | false | boolean                                  | default=false                                       |
| `computer_use_permission_timeout_ms` | false | integer                                  | exclusiveMinimum=0; default=120000                  |
| `monitoring`                         | false | LocalEnvdMonitoringConfiguration 或 null | default=null                                        |
| `trusted_executable_roots`           | false | string 数组                              | default=[]                                          |
| `shell_profiles`                     | false | 数组，元素类型为 LocalEnvdShellProfile   | default=[]                                          |
| `max_file_bytes`                     | false | integer                                  | exclusiveMinimum=0; default=16777216                |
| `max_output_preview_bytes`           | false | integer                                  | maximum=16777216; exclusiveMinimum=0; default=65536 |
| `max_output_bytes_per_stream`        | false | integer                                  | exclusiveMinimum=0; default=268435456               |
| `max_spool_bytes`                    | false | integer                                  | exclusiveMinimum=0; default=1073741824              |
| `max_device_spool_bytes`             | false | integer                                  | exclusiveMinimum=0; default=4294967296              |

## `EnvdExecutionConfiguration`

| 字段         | 必填  | 类型 / 可选值   | 约束与默认值                                         |
| ------------ | ----- | --------------- | ---------------------------------------------------- |
| `uid`        | false | integer 或 null | minimum=0; exclusiveMaximum=4294967295; default=null |
| `gid`        | false | integer 或 null | minimum=0; exclusiveMaximum=4294967295; default=null |
| `allow_sudo` | false | boolean         | default=true                                         |

## `EnvdNetworkConfiguration`

| 字段   | 必填 | 类型 / 可选值                   | 约束与默认值 |
| ------ | ---- | ------------------------------- | ------------ |
| `mode` | true | "inherit", "deny", "controlled" | —            |

## `LocalEnvdMonitoringConfiguration`

可选的外层守护进程采样配置，独立于会话执行策略。

| 字段                  | 必填  | 类型 / 可选值  | 约束与默认值                               |
| --------------------- | ----- | -------------- | ------------------------------------------ |
| `sample_interval_ms`  | false | integer        | minimum=1000; maximum=60000; default=10000 |
| `filesystems`         | false | object         | —; 默认值由模型工厂计算                    |
| `cgroup_v2_directory` | false | string 或 null | format="path"; default=null                |

## `LocalEnvdShellProfile`

| 字段               | 必填  | 类型 / 可选值 | 约束与默认值                        |
| ------------------ | ----- | ------------- | ----------------------------------- |
| `profile_id`       | true  | string        | minLength=1; maxLength=128          |
| `executable`       | true  | string        | format="path"                       |
| `fixed_arguments`  | false | string 数组   | default=[]                          |
| `allow_login`      | false | boolean       | default=false                       |
| `max_script_bytes` | false | integer       | exclusiveMinimum=0; default=1048576 |

## `DockerRecipe`

| 字段                          | 必填  | 类型 / 可选值                  | 约束与默认值                                                                     |
| ----------------------------- | ----- | ------------------------------ | -------------------------------------------------------------------------------- |
| `image`                       | false | string                         | minLength=1; maxLength=1024; default="ghcr.io/converge-ai-labs/a13n-sandbox:dev" |
| `execution`                   | false | "native", "envd_http"          | default="native"                                                                 |
| `desktop`                     | false | boolean                        | default=false                                                                    |
| `mounts`                      | false | 数组：DockerMountConfiguration | default=[]                                                                       |
| `environment`                 | false | EnvironmentVariables           | —; 默认值由模型工厂计算                                                          |
| `init_script`                 | false | string 或 null                 | maxLength=1048576; format="multiline"; default=null                              |
| `disable_network`             | false | boolean                        | default=false                                                                    |
| `user`                        | false | string 或 null                 | minLength=1; maxLength=128; default=null                                         |
| `shell`                       | false | string                         | default="/bin/sh"                                                                |
| `python`                      | false | string                         | default="python3"                                                                |
| `cpus`                        | false | number 或 null                 | minimum=0.001; default=null                                                      |
| `memory_gb`                   | false | number 或 null                 | minimum=0.006291456; default=null                                                |
| `pids_limit`                  | false | integer 或 null                | exclusiveMinimum=0; default=null                                                 |
| `stop_grace_seconds`          | false | integer                        | minimum=0; maximum=300; default=10                                               |
| `request_timeout_seconds`     | false | integer                        | maximum=3600; exclusiveMinimum=0; default=60                                     |
| `max_file_bytes`              | false | integer                        | exclusiveMinimum=0; default=16777216                                             |
| `max_query_entries`           | false | integer                        | exclusiveMinimum=0; default=100000                                               |
| `max_output_preview_bytes`    | false | integer                        | exclusiveMinimum=0; default=65536                                                |
| `max_output_bytes_per_stream` | false | integer                        | exclusiveMinimum=0; default=16777216                                             |
| `max_spool_bytes`             | false | integer                        | exclusiveMinimum=0; default=67108864                                             |
| `max_concurrent_processes`    | false | integer                        | exclusiveMinimum=0; default=128                                                  |
| `desktop_assistance`          | false | boolean                        | default=false                                                                    |
| `desktop_viewer_url`          | false | string 或 null                 | maxLength=2048; default=null                                                     |
| `pull_policy`                 | false | "never", "if_missing"          | default="if_missing"                                                             |

## `DockerMountConfiguration`

| 字段        | 必填  | 类型 / 可选值 | 约束与默认值  |
| ----------- | ----- | ------------- | ------------- |
| `source`    | true  | string        | —             |
| `target`    | true  | string        | format="path" |
| `read_only` | false | boolean       | default=true  |

## `E2BRecipe`

| 字段                        | 必填  | 类型 / 可选值         | 约束与默认值                                              |
| --------------------------- | ----- | --------------------- | --------------------------------------------------------- |
| `template`                  | false | string                | minLength=1; maxLength=256; default="base"                |
| `execution`                 | false | "native", "envd_http" | default="native"                                          |
| `desktop`                   | false | boolean               | default=false                                             |
| `environment`               | false | EnvironmentVariables  | —; 默认值由模型工厂计算                                   |
| `root`                      | false | string                | default="/home/user"                                      |
| `user`                      | false | string                | `pattern="^[a-z_][a-z0-9_-]{0,63}$"`; default="user"      |
| `python`                    | false | string                | default="/usr/bin/python3"                                |
| `request_timeout_seconds`   | false | number                | maximum=300; exclusiveMinimum=0; default=30               |
| `allow_internet_access`     | false | boolean               | default=true                                              |
| `max_file_bytes`            | false | integer               | maximum=1073741824; exclusiveMinimum=0; default=104857600 |
| `max_observation_bytes`     | false | integer               | maximum=16777216; exclusiveMinimum=0; default=1048576     |
| `max_active_observations`   | false | integer               | maximum=1024; exclusiveMinimum=0; default=128             |
| `max_retained_output_bytes` | false | integer               | maximum=1073741824; exclusiveMinimum=0; default=134217728 |
| `max_query_entries`         | false | integer               | maximum=100000; exclusiveMinimum=0; default=10000         |
| `desktop_assistance`        | false | boolean               | default=false                                             |
| `timeout_seconds`           | false | integer               | minimum=30; maximum=86400; default=3600                   |

## `DaytonaEnvironmentConfiguration`

| 字段                      | 必填  | 类型 / 可选值        | 约束与默认值                                 |
| ------------------------- | ----- | -------------------- | -------------------------------------------- |
| `snapshot`                | true  | string               | minLength=1; maxLength=256                   |
| `desktop`                 | false | boolean              | default=false                                |
| `environment`             | false | EnvironmentVariables | —; 默认值由模型工厂计算                      |
| `request_timeout_seconds` | false | number               | maximum=600; exclusiveMinimum=0; default=120 |

## `ModalEnvironmentConfiguration`

| 字段                      | 必填  | 类型 / 可选值        | 约束与默认值                                 |
| ------------------------- | ----- | -------------------- | -------------------------------------------- |
| `image`                   | true  | string               | minLength=1; maxLength=256                   |
| `desktop`                 | false | boolean              | default=false                                |
| `environment`             | false | EnvironmentVariables | —; 默认值由模型工厂计算                      |
| `request_timeout_seconds` | false | number               | maximum=600; exclusiveMinimum=0; default=120 |
| `timeout_seconds`         | false | integer              | minimum=300; maximum=86400; default=86400    |
| `cpu`                     | false | number               | maximum=64; exclusiveMinimum=0; default=1    |
| `memory`                  | false | integer              | minimum=128; maximum=262144; default=1024    |

## `VercelEnvironmentConfiguration`

| 字段                      | 必填  | 类型 / 可选值         | 约束与默认值                                                                        |
| ------------------------- | ----- | --------------------- | ----------------------------------------------------------------------------------- |
| `environment`             | false | EnvironmentVariables  | —; 默认值由模型工厂计算                                                             |
| `root`                    | false | string                | default="/vercel/sandbox"                                                           |
| `python`                  | false | string                | default="/vercel/runtimes/python/bin/python3"                                       |
| `shell`                   | false | string                | default="/bin/bash"                                                                 |
| `request_timeout_seconds` | false | number                | maximum=600; exclusiveMinimum=0; default=120                                        |
| `max_file_bytes`          | false | integer               | maximum=67108864; exclusiveMinimum=0; default=16777216                              |
| `max_query_entries`       | false | integer               | maximum=100000; exclusiveMinimum=0; default=10000                                   |
| `max_output_bytes`        | false | integer               | maximum=16777216; exclusiveMinimum=0; default=1048576                               |
| `execution`               | false | "native", "envd_http" | default="native"                                                                    |
| `snapshot`                | false | string 或 null        | minLength=1; maxLength=256; `pattern="^(auto\|snap_[A-Za-z0-9_-]+)$"`; default=null |
| `desktop`                 | false | boolean               | default=false                                                                       |
| `runtime`                 | false | string                | minLength=1; maxLength=128; default="python3.13"                                    |
| `timeout_seconds`         | false | integer               | minimum=300; maximum=18000; default=3600                                            |
| `vcpus`                   | false | integer               | minimum=2; maximum=8; default=2                                                     |

## `SpritesEnvironmentConfiguration`

| 字段                      | 必填  | 类型 / 可选值          | 约束与默认值                                           |
| ------------------------- | ----- | ---------------------- | ------------------------------------------------------ |
| `environment`             | false | EnvironmentVariables   | —; 默认值由模型工厂计算                                |
| `root`                    | false | string                 | default="/home/sprite"                                 |
| `python`                  | false | string                 | default="python3"                                      |
| `shell`                   | false | string                 | default="/bin/bash"                                    |
| `request_timeout_seconds` | false | number                 | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false | integer                | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false | integer                | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false | integer                | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `region`                  | false | string 或 null         | maxLength=64; default=null                             |
| `execution`               | false | "native" / "envd_http" | default="native"                                       |
| `desktop`                 | false | boolean                | default=false                                          |

## `RunloopEnvironmentConfiguration`

| 字段                      | 必填  | 类型 / 可选值                                    | 约束与默认值                                           |
| ------------------------- | ----- | ------------------------------------------------ | ------------------------------------------------------ |
| `environment`             | false | EnvironmentVariables                             | —; 默认值由模型工厂计算                                |
| `root`                    | false | string                                           | default="/home/user"                                   |
| `python`                  | false | string                                           | default="python3"                                      |
| `shell`                   | false | string                                           | default="/bin/bash"                                    |
| `request_timeout_seconds` | false | number                                           | maximum=600; exclusiveMinimum=0; default=120           |
| `max_file_bytes`          | false | integer                                          | maximum=67108864; exclusiveMinimum=0; default=16777216 |
| `max_query_entries`       | false | integer                                          | maximum=100000; exclusiveMinimum=0; default=10000      |
| `max_output_bytes`        | false | integer                                          | maximum=16777216; exclusiveMinimum=0; default=1048576  |
| `blueprint_id`            | false | string 或 null                                   | maxLength=128; default=null                            |
| `resource_size`           | false | "X_SMALL", "SMALL", "MEDIUM", "LARGE", "X_LARGE" | default="SMALL"                                        |
| `idle_timeout_seconds`    | false | integer                                          | minimum=300; maximum=172800; default=3600              |

## `RemoteEnvdEnvironmentConfiguration`

| 字段                | 必填  | 类型 / 可选值                   | 约束与默认值                             |
| ------------------- | ----- | ------------------------------- | ---------------------------------------- |
| `egress`            | false | EnvdEgressConfiguration 或 null | default=null                             |
| `expected_boundary` | false | EnvdBoundaryRequirement 或 null | default=null                             |
| `working_directory` | false | string 或 null                  | format="eip-absolute-path"; default=null |
| `required_methods`  | false | string 数组                     | maxItems=128; default=[]                 |

## `HostLocalProviderConfiguration`

| 字段      | 必填  | 类型 / 可选值 | 约束与默认值                                     |
| --------- | ----- | ------------- | ------------------------------------------------ |
| `host_id` | false | string        | minLength=1; maxLength=256; 默认值由模型工厂计算 |

## `DockerConnectionConfiguration`

| 字段          | 必填  | 类型 / 可选值                   | 约束与默认值                      |
| ------------- | ----- | ------------------------------- | --------------------------------- |
| `envd_http`   | false | DockerHttpConfiguration 或 null | default=null                      |
| `docker_host` | false | string                          | minLength=1; 默认值由模型工厂计算 |

## `DockerHttpConfiguration`

| 字段                           | 必填  | 类型 / 可选值  | 约束与默认值                                            |
| ------------------------------ | ----- | -------------- | ------------------------------------------------------- |
| `host`                         | true  | string         | minLength=1; maxLength=253                              |
| `bind_host`                    | false | string 或 null | default=null                                            |
| `allow_plaintext_private_link` | false | boolean        | default=false                                           |
| `credential_file`              | false | string         | maxLength=4096; default="/var/lib/a13n-envd/http-token" |

## `E2BConnectionConfiguration`

| 字段      | 必填  | 类型 / 可选值  | 约束与默认值                                                               |
| --------- | ----- | -------------- | -------------------------------------------------------------------------- |
| `domain`  | false | string         | `pattern="^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$"`; default="e2b.dev" |
| `api_url` | false | string 或 null | default=null                                                               |

## `E2BCredential`

| 字段      | 必填 | 类型 / 可选值 | 约束与默认值                   |
| --------- | ---- | ------------- | ------------------------------ |
| `api_key` | true | string        | minLength=1; format="password" |

## `DaytonaConnectionConfiguration`

| 字段              | 必填  | 类型 / 可选值  | 约束与默认值                             |
| ----------------- | ----- | -------------- | ---------------------------------------- |
| `organization_id` | false | string or null | minLength=1; maxLength=128; default=null |
| `target`          | false | string         | minLength=1; maxLength=64; default="us"  |

## `ModalConnectionConfiguration`

| 字段               | 必填  | 类型 / 可选值  | 约束与默认值                                         |
| ------------------ | ----- | -------------- | ---------------------------------------------------- |
| `workspace`        | false | string 或 null | minLength=1; maxLength=128; default=null             |
| `app_name`         | false | string         | minLength=1; maxLength=128; default="a13n-sandboxes" |
| `environment_name` | false | string         | minLength=1; maxLength=128; default="main"           |

## `ModalCredential`

| 字段           | 必填 | 类型 / 可选值 | 约束与默认值                   |
| -------------- | ---- | ------------- | ------------------------------ |
| `token_id`     | true | string        | minLength=1; format="password" |
| `token_secret` | true | string        | minLength=1; format="password" |

## `VercelConnectionConfiguration`

| 字段         | 必填 | 类型 / 可选值 | 约束与默认值               |
| ------------ | ---- | ------------- | -------------------------- |
| `team_id`    | true | string        | minLength=1; maxLength=128 |
| `project_id` | true | string        | minLength=1; maxLength=128 |

## `SpritesConnectionConfiguration`

| 字段           | 必填 | 类型 / 可选值 | 约束与默认值               |
| -------------- | ---- | ------------- | -------------------------- |
| `organization` | true | string        | minLength=1; maxLength=128 |

## `RunloopConnectionConfiguration`

| 字段           | 必填 | 类型 / 可选值 | 约束与默认值               |
| -------------- | ---- | ------------- | -------------------------- |
| `organization` | true | string        | minLength=1; maxLength=128 |

## `TokenCredential`

| 字段      | 必填 | 类型 / 可选值 | 约束与默认值                   |
| --------- | ---- | ------------- | ------------------------------ |
| `api_key` | true | string        | minLength=1; format="password" |

## `HttpEnvdConnectionConfiguration`

| 字段                           | 必填  | 类型 / 可选值 | 约束与默认值                                 |
| ------------------------------ | ----- | ------------- | -------------------------------------------- |
| `initialization_timeout`       | false | number        | maximum=300; exclusiveMinimum=0; default=10  |
| `request_timeout`              | false | number        | maximum=3600; exclusiveMinimum=0; default=30 |
| `max_in_flight`                | false | integer       | minimum=1; maximum=1024; default=32          |
| `endpoint`                     | true  | string        | minLength=1; maxLength=2048                  |
| `allow_plaintext_private_link` | false | boolean       | default=false                                |

## `HttpEnvdCredential`

| 字段      | 必填  | 类型 / 可选值 | 约束与默认值                                   |
| --------- | ----- | ------------- | ---------------------------------------------- |
| `headers` | false | object        | —; default from model factory                  |
| `token`   | true  | string        | minLength=1; maxLength=4096; format="password" |

## `WebSocketEnvdConnectionConfiguration`

| 字段                 | 必填  | 类型 / 可选值 | 约束与默认值                                |
| -------------------- | ----- | ------------- | ------------------------------------------- |
| `connection_timeout` | false | number        | maximum=300; exclusiveMinimum=0; default=10 |
