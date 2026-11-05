---
title: 环境
description: 为文件和命令任务创建工作空间、复用已有机器，或给 agent 配置桌面。
---

任务需要文件、命令或桌面时，为 agent 配置环境。先用一个模板和一个线程走通流程：让 agent 写入文件，再在后续消息中读取。这样可以同时验证执行能力和文件复用。

三个资源连接起这条流程：

| 资源     | 你选择的内容                   | 结果                       |
| -------- | ------------------------------ | -------------------------- |
| Provider | Docker Engine 或云账号         | Service 可以在其中管理资源 |
| 模板     | 镜像、资源、环境变量和空闲策略 | 用于创建托管实例的配方     |
| 线程挂载 | 实例、名称、目录和权限         | 后续运行可以使用该实例     |

已有自行维护的计算机时，改为[注册其 Envd 端点](#register-an-external-target)。Service 负责连接，计算机的生命周期由你管理。每次接收运行都会固定其挂载和权限，因此修改对后续运行生效。

## 工作空间自动配置

创建资源前，先检查**环境 → 模板**。挂载 Docker socket 的单机 Compose 部署会在 Engine 可达时准备 **Docker** provider 和 **Linux Sandbox** 模板。首次文件任务可以直接选择该模板。镜像包含 Python、Node.js、Git 和常用开发工具；命令以 `sandbox` 用户运行，可写目录为 `/workspace` 和 `/tmp/a13n`。

不挂载 socket 的快速入门部署不会自动配置 Docker。源码开发的 `make dev` 会启用 **Local**，在检出目录的 `var/dev/environments` 下创建 **Local Workspace** 模板。每个实例使用独立子目录。Local 命令直接以 Service 进程的操作系统用户身份执行，不提供隔离。

通用 Service 设置默认关闭这两种自动配置。启用它们或使用本地构建镜像时，参阅[工作空间自动配置](workspace-provisioning.md#workspace-provisioning)。`all` 角色在创建工作空间和启动时补建缺失的默认资源。失败时，修复 Engine 访问并重启。成功配置后会保留后续模板修改、禁用和删除结果。自动配置创建的是配置资源；实际实例在收到请求后分配。

## Provider

在**工作空间设置 → 供应商 → 环境**中添加或编辑环境 provider。你需要工作空间 `write` 权限，以及所选 Engine 或云账号的访问权限。模板中的**管理供应商**会打开同一列表，并保留模板草稿。

1. 选择运行方式，填写账号设置和凭据。
2. 保存 provider，并保持启用。
3. Docker 可测试连接。测试只 ping Engine；创建实例才能验证镜像与执行配置。
4. 创建模板时选择该 provider。

![Console 中的 Sandbox 与环境 provider 目录](../../.github/assets/console-sandbox-providers.webp)

[Provider 指南](../environments/providers.md)介绍 Docker、E2B、Daytona、Modal、Vercel、Sprites、Runloop 和 Local 的配置；[配置参考](../environments/configuration-reference.md)列出各自的 recipe 字段。API 客户端通过 `/api/v1/environment-providers` 创建 provider，通过 `GET /api/v1/provider-types/environment` 获取类型 schema。

Docker 默认使用运维设置的 `environments.docker_host`。provider 单独指定的地址必须是[出站策略](configuration.md#outbound-requests)允许的远程 TCP 或 HTTPS Engine。Engine 访问授予宿主机级权限。宿主机绑定源必须位于 `environments.docker_mount_roots` 下，该设置默认不允许任何路径；不提供特权容器或宿主机命名空间。

云实例使用已保存的账号，并向该账号计费。配置空闲停止或删除来限制保留时间。Service 会续期受支持的就绪沙箱，但仍受厂商硬性时限约束。已停止的实例可以带着保留的文件恢复；丢失的实例需要恢复或替换。Sprites 不支持显式停止，需要时使用空闲删除。

编辑 provider 时，省略凭据会保留原值，新值会替换原值，`null` 会移除凭据。保留的模板或实例引用会阻止删除 provider。删除无引用的 provider 只移除已保存的配置和凭据，不删除底层 Engine 或云账号。

## Kubernetes 沙箱

Kubernetes 将实例运行为原生 Pod，由选定容器中的 Envd 提供执行能力。运维配置受信任的集群 profile、允许的命名空间和沙箱 ServiceAccount 后，Kubernetes 才会出现在 provider 目录中。工作空间用户选择 profile 和命名空间；Kubernetes API 凭据由运维持有。

1. 请运维在 Control 和 Worker 上配置 `environments.kubernetes_clusters`，或按 [Kubernetes 运维配置](configuration.md#kubernetes-environments)中的说明启用 Helm 选项。
2. 在**工作空间设置 → Providers → 环境**中创建 Kubernetes provider。选择运维配置的集群 profile，并填写获准的命名空间。Helm 示例对应 `config: {"cluster":"in-cluster","namespace":"agent-sandboxes"}`。保存 provider 后，重新打开并选择 **Check connection（检查连接）**。
3. 按下方说明准备沙箱镜像，然后创建模板。选择 **Pod 直连**或**本地端口转发**，通过卡片配置容器、资源请求与限制、变量、持久存储和调度。**生命周期**单独控制回收策略。常规配置无需编写 YAML 或 JSON；**编辑原生 JSON**提供其他原生字段，返回表单后修改配置也会保留这些字段。下方 JSON 是等价的 API 模板 `config`。
4. 在已配置可用模型的 agent 上启用 **Files** 和 **Shell**，并选择此默认模板。新建会话，让 agent 写入 `hello.txt`，再发一条消息读取文件。只覆盖一次运行时，选择**运行选项 → 本次运行环境 → 从模板创建**。

```json
{
  "recipe": {
    "execution_container": "envd",
    "connection": "direct",
    "pod_template": {
      "spec": {
        "restartPolicy": "Never",
        "containers": [{"name": "envd", "image": "a13n-sandbox:local"}]
      }
    }
  },
  "reclamation": {
    "idle_delete_seconds": 1800,
    "preparation_seconds": 600,
    "failed_retention_seconds": 3600
  }
}
```

**首次运行前先准备镜像。** 示例中的 `a13n-sandbox:local` 是本地构建，不是仓库镜像。在本 checkout 中构建并加载到已有 kind 集群（将 `my-cluster` 替换为实际名称）：

```sh
make image-sandbox SANDBOX_IMAGE=a13n-sandbox:local
kind load docker-image a13n-sandbox:local --name my-cluster
```

其他集群需要按节点架构构建本 checkout 的沙箱镜像并推送到仓库，再把准确的 tag 或 digest 写入 `pod_template.spec.containers[].image`：

```sh
make image-sandbox SANDBOX_IMAGE=registry.example.com/team/a13n-sandbox:reviewed
# Authenticate to your registry before pushing.
docker push registry.example.com/team/a13n-sandbox:reviewed
```

私有仓库还需配置原生 `imagePullSecrets`。镜像必须启动 Envd，并使用 Service 注入的凭据文件路径；使用与 Service 相同 checkout 构建的沙箱镜像。`make k8s-up K8S_ENVIRONMENTS=1` 会构建并加载自己的镜像 tag；请读取其 `K8S_STATE_DIR` 下 `deployment.json` 中的 `sandbox_image`，不要假定它是 `:local`。默认目录是 `~/.config/a13n-service-kind`；使用非默认 `K8S_CLUSTER` 时追加 `-<cluster>`。

资源请求与限制、init 容器、sidecar、调度、安全上下文及 ConfigMap、Secret 引用保留在 `pod_template.spec` 中，由 Kubernetes admission 校验这些原生字段。Service 管理 Pod 身份、沙箱 ServiceAccount 和私有 Envd 凭据注入。选定的执行容器必须启动 Envd；Pod 处于 Running 并不代表执行能力已经就绪。

`direct` 从 Service 连接 Pod IP，Service 可以部署在集群内或集群外。集群外部署时，运维在 Control 和 Worker 上提供 kubeconfig profile。直连要求两个进程都能访问 Pod IP。没有 Pod IP 路由的本地调试可使用 `port_forward`，前提是 kubeconfig profile 明确允许且已安装 `kubectl`；隧道仅属于使用它的进程。两种方式目前都使用 Bearer 认证的 HTTP，需要 profile 显式设置 `allow_plaintext_envd = true`。此类流量应保留在可信私有网络内。provider 检查成功代表配置、认证和 Pod 列举权限正常；镜像拉取、Pod 创建、调度、存储和 Envd 就绪仍需通过真实实例验证。

需要持久文件时，在 `recipe.storage` 中添加绑定，包含 `name`、`mount_path`，以及原生 `claim_template` 或 `existing_claim` 二选一。新建 PVC 默认使用 `disposition: retain`；选择 `delete` 会在回收时删除拥有的 PVC。已有 PVC 始终视为借用资源，即使绑定指定删除也会保留。`shared_directory: true` 在已有 PVC 中使用实例专属目录，并保留该目录。引用的 ConfigMap 和 Secret 也属于借用资源。`recipe.network_policy` 接受原生 NetworkPolicy 模板，Pod selector 由 Service 填入；实际执行网络策略需要兼容的集群网络实现。

Kubernetes 支持创建、复用和删除。回收设置在预留实例时复制到实例。默认空闲 30 分钟后删除，准备时间上限为 10 分钟，准备失败后保留一小时。Kubernetes 没有停止/恢复操作。删除会等待所拥有的资源消失；finalizer 可能使实例保持 `deleting`。保留和借用的资源仍可在回收记录中查看。

在实例的 Kubernetes 面板查看 Pod UID、调度和容器状态、Events、存储处置与 Envd 就绪状态。`GET /api/v1/environments/{id}/kubernetes/diagnostics` 和 `/kubernetes/logs` 需要工作空间 `run` 权限。日志有大小限制，可选择容器和上一轮容器日志。这些查询不重置空闲计时。调试时，可用当前 `If-Match` 向 `PUT /api/v1/environments/{id}/debug-hold` 提交 `{"seconds":3600}`，暂缓自动回收；`{"seconds":null}` 清除保留。每次保留最长一天。Pod 被替换且 UID 改变时属于目标丢失，应创建新实例，不会静默接管替代 Pod。

## 仅本次运行的环境选择

会话的运行选项可以为一次运行新建工作空间，或选择已有实例。API 客户端提交 `options.environment`：

```json
{"mode":"fresh","template_id":"envtpl_..."}
```

```json
{"mode":"existing","environment_id":"env_..."}
```

接收后的运行固定实例 ID 和权限。同一次已接收运行的重试保留原选择。覆盖只替换本次运行的 `workspace` 挂载，不修改线程的期望挂载。省略该选项时，继续使用正常的线程挂载和 agent 默认行为。选择不可用的已有实例会失败，不会自动创建替代实例。

## 模板

通过 Console 创建第一个工作空间：

1. 打开**环境 → 模板 → 创建模板**，命名为 **Python sandbox**。
2. 选择 Docker 和已启用的 provider，设置资源限制和空闲策略后保存。保存模板不会启动容器。
3. 编辑一个已经能调用模型的 agent，启用其 **Files** 和 **Shell** 工具集，选择此默认环境模板并保存 agent 配置。
4. 开始新会话，要求：“在工作空间写入 `hello.txt`，内容为 `Hello from Service`，然后读回。”检查工具结果。
5. 发送后续消息，要求再次读取同一文件。agent 应使用该线程原有的工作空间。

agent 字段为 `default_environment_template_id`。根线程没有 `workspace` 挂载时，Service 在接收运行时预留实例，并以 `workspace` 挂载。`reserved` 实例有 ID，但还没有厂商资源，首次使用才准备环境。预留记录计入托管环境额度。

API 配置使用[直接 HTTP 集成](connect-application.md#direct-http-integration)中的 URL 和工作空间 API Key。将 `eprov_...` 替换为 Docker provider ID。创建或编辑模板需要 `write`：

```sh
curl -sS -X POST "$A13N_URL/api/v1/environment-templates" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Python sandbox","provider_id":"eprov_...",
       "config":{"recipe":{"cpus":2,"memory_gb":4},
                 "stop_after_seconds":1800,"delete_after_seconds":604800}}'
```

保存返回的模板 ID。此例在空闲 30 分钟后停止实例，七天后删除实例。两个计时器都从最后使用时间起算；`null` 关闭对应策略。空闲删除会移除线程挂载和实例文件。配置了默认模板的线程可以在下次运行时获得新工作空间。

更新模板时，将模板 ETag 放入 `If-Match`。Recipe 在首次认领创建任务时固定，应在此之前修改，或创建新实例。使用 `stop_after_seconds` 和 `delete_after_seconds` 的 provider，其空闲策略修改立即适用于已有实例。Kubernetes 的 `reclamation` 在预留时固定；修改仅影响新的预留。禁用模板会阻止新预留，也会阻止尚处于预留状态的实例首次创建；已经创建的实例继续可用。

### 默认环境变量

在模板的**环境变量**中添加普通命令默认值，例如 `APP_MODE=agent`。API 位置为 `config.recipe.environment`：

```json
{"environment":{"APP_MODE":"agent","TOOL_ENDPOINT":"https://tools.example.com"}}
```

这是 recipe 片段。命令及其子进程接收这些值。[执行策略](execution-options.md#execution-policy)和命令显式指定的值可以覆盖默认值，而不修改共享实例。Local 还要求变量名列入 `allowed_environment_keys`，除非该白名单不设限制。

使用可移植的变量名和字符串值。秘密值应保留在凭据或 secret binding 控制中：模板变量是可读取的配置。平台启动、桌面和遥测变量名由平台保留。默认变量随实例 recipe 固定，停止后启动仍保留原值。要应用修改后的模板默认值，请创建新实例。

### Docker 镜像版本

省略 `recipe.image` 时，Service 选择审定的 `ghcr.io/converge-ai-labs/a13n-sandbox` 镜像：

| Service 构建                            | 镜像标签 |
| --------------------------------------- | -------- |
| 稳定版或 RC                             | `0.5.1`  |
| 源码（`0.0.0`，包括本地后缀）或 `.devN` | `dev`    |

Sandbox 版本归 Envd 管理，与 Service 版本独立。设置明确的标签或 digest 可选择其他镜像。`pull_policy: if_missing` 使用 Engine 本地镜像，缺失时在首次创建时拉取；`never` 要求镜像已经存在。自动配置的模板保存明确镜像，因此升级会保留这一选择。

实例在重试和升级中保留已解析的镜像。要使用新镜像，请重新创建实例。未保存镜像的旧 handle 保留 `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`；移除旧镜像前应重新创建这些实例。

## 实例

打开**环境 → 实例**查看创建和使用状态。API 客户端使用 `GET /api/v1/environments`，或按 ID 获取单个实例。列表只有在 `status=deleted` 时才包含已删除记录。

| 状态                   | 下一步                           |
| ---------------------- | -------------------------------- |
| `reserved`             | 在运行中使用，触发准备           |
| `creating`, `starting` | 等待就绪；有失败时查看原因       |
| `ready`                | 挂载并执行任务                   |
| `stopping`, `stopped`  | 等待停止完成；后续运行会再次启动 |
| `deleting`, `deleted`  | 选择其他实例                     |

失败信息包含代码、消息和操作结果是否确定。Service 使用原有操作标识核对未完成操作。出现 `provider_credential_changed` 时，恢复实例原账号的访问。如果供应方确认线程的 `workspace` 挂载对应的模板环境已经丢失（`environment_lost`），下次使用该挂载的运行会从同一模板获得新实例。旧沙箱中的文件和进程无法恢复。新沙箱的启动时机仍由运行的 `lazy_environment` 设置决定。已经使用旧实例的运行可能失败。

原本共享同一实例的线程（包括 fork）会在各自的下一次运行中使用同一个替代实例。如果实例再次丢失，较晚恢复运行的线程会接入最新实例。替代实例沿用原来的配额，因此工作空间的配额只有 1 时也能连续恢复。

如果应用需要原来的磁盘，请在创建会话时设置 `"auto_recreate_lost_environment": false`，也可以通过 `PATCH /api/v1/sessions/{id}` 修改，请求需携带会话当前的 `If-Match`。默认值为 `true`。指定确切实例的运行、共享父环境的子智能体及其他名称的挂载仍会报告原实例的故障。

### 预留托管环境

需要在任务前检查实例，或在线程之间共享时，可以显式创建。替换模板占位符：

```sh
curl -sS -X POST "$A13N_URL/api/v1/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"envtpl_...","name":"Shared data"}'
```

此操作需要 `run`，并立即开始创建：响应状态为 `creating`，随后在后台转为 `ready`。将返回的 `id` 保存为 `ENVIRONMENT`，查看 `GET /api/v1/environments/$ENVIRONMENT`，再按下文挂载。这与接收运行时的延迟预留不同。两种方式都计入 `environments.managed_count`；超过限制返回 `409 conflict`，原因为 `environment_limit`。

### 注册外部目标

1. 在目标计算机运行 [HTTP Envd 守护进程](../environments/remote-envd.md#connect-to-an-existing-http-daemon)，使用稳定的设备 ID 和 token。
2. 确保 Service worker 可以访问端点，且出站策略允许。明文 HTTP 只接受回环地址，其他地址使用 HTTPS。
3. 使用工作空间 `write` 权限注册 origin 和 token：

```sh
curl -sS -X POST "$A13N_URL/api/v1/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"endpoint":"https://build-box.example.com:8443","token":"YOUR_ENVD_TOKEN","name":"Build box"}'
```

Service 验证设备，加密保存 token，并返回 `ready` 实例。使用注册时的用户或服务账号挂载：外部目标的执行权限仅属于注册主体。所有者或工作空间管理员可以管理此注册。

轮换 token 或迁移端点时，向 `PATCH /api/v1/environments/{id}` 提交环境 ETag。修改端点时同时提交 token。Service 验证新连接仍指向同一设备；设备不同时以 `provider_device_mismatch` 拒绝。删除注册会忘记 token，计算机继续运行。

Worker 恢复会尽力复用检查点中的 Envd 会话。会话已关闭或过期时需要新建。恢复不会还原运行内的进程引用或输出观测；重试结果不确定的命令前，先检查当前状态。正常清理会关闭会话，后续运行使用独立会话。

### 停止、重命名与删除

先读取环境，再将其 ETag 放入 `If-Match`。以下操作需要 `write`：

| 操作                                                          | 结果                                 |
| ------------------------------------------------------------- | ------------------------------------ |
| `PATCH /api/v1/environments/{id}`，传入 `{"name":"New name"}` | 重命名实例                           |
| `POST /api/v1/environments/{id}/stop`                         | 停止受支持且就绪的托管实例，保留文件 |
| `DELETE /api/v1/environments/{id}`                            | 销毁托管实例，或忘记外部注册         |

停止或删除前，结束活跃运行和桌面控制。删除还需要先移除线程挂载。永久失败的实例是例外：删除时会一并移除挂载。未确定结果的操作可能以 `operation_unresolved` 阻止删除。只移除挂载会保留实例；删除托管实例会销毁文件。

## 在线程上挂载环境

通过挂载将实例连接到已有的开放线程：

1. 将 `THREAD` 设为线程 ID，`ENVIRONMENT` 设为实例 ID。
2. 读取 `GET /api/v1/threads/$THREAD/environments`，将响应 ETag 保存为 `THREAD_ETAG`，保留引号。
3. 添加挂载。省略 `working_directory` 可使用 provider 默认目录：

```sh
curl -sS -X POST "$A13N_URL/api/v1/threads/$THREAD/environments" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d "{\"name\":\"data\",\"environment_id\":\"$ENVIRONMENT\"}"
```

4. 发送新消息，要求 agent 列出 `data` 中的文件。检查工具调用选择的挂载。先前已接收的运行保留原挂载。

修改挂载需要 `run` 和**线程**的 ETag。`workspace` 显示为 `/workspace`；`data` 等命名挂载显示为 `/mnt/data`。同一线程中，每个实例和名称只能挂载一次。替换前，通过 `DELETE /api/v1/threads/{thread_id}/environments/{name}` 移除原挂载。多个线程可以同时共享实例及其文件。

默认挂载为 `workspace`；没有 `workspace` 时，唯一的用户挂载作为默认挂载。存在多个命名挂载且没有 `workspace` 时，没有默认挂载。首次模型请求前，Service 读取默认目录中可用的 `AGENTS.md`，并列出附近的指导文件和 skill 目录，供按需查看。这可能在工具调用前激活默认挂载。附件和依赖环境的 skill 也可能触发准备。

`lazy_environment` 默认为 `true`。首次使用会创建预留实例或启动已停止实例，最多等待 `environments.wait_seconds`。设为 `false` 可在执行前准备全部挂载实例；执行连接仍在首次相关使用时打开。单次消息可以这样覆盖：

```json
{"options":{"overrides":{"lazy_environment":false}}}
```

重试和继续执行保留已接受的选择。分叉默认共享挂载，除非选择 `fresh_environments`；归档会移除线程挂载。异步[子 agent](agent-configuration.md#subagents) 使用调用边的 `shared`、`dedicated` 或无环境策略。内联子 agent 共享父运行环境。

### 复用实例并选择项目目录

新会话的**选项**中选择**复用现有实例**，可填写**工作目录**。已有会话使用**环境与记忆 → 添加环境**。留空即使用 provider 默认目录。

选择环境内已经存在的路径。Local 的 `/projects/app` 表示 `{模板根目录}/{环境 ID}/projects/app`，不是浏览器所在计算机的路径。Service 在激活时检查目录；目录不存在或不可访问时报告错误，不会创建目录。对应 API 字段是创建挂载时的 `working_directory`。

相对文件、挂载路由和命令 cwd 都使用该目录。两个线程可以在同一实例的不同目录组织工作，但仍共享进程、端口、已安装软件和操作系统权限。任务需要隔离时，选择提供隔离的后端，并为每个任务使用独立实例。

## Computer Use

<span id="create-a-docker-desktop"></span> <span id="automatic-vercel-desktops"></span> <span id="enable-observation-and-interaction"></span> <span id="computer-coordinate-mode"></span> <span id="desktop-access-prerequisites"></span> <span id="get-and-return-desktop-control"></span> <span id="upgrade-desktop-control"></span> <span id="automatic-sprites-desktops"></span>

- [使用桌面](computer-use.md)
