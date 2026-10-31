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

通用 Service 设置默认关闭这两种自动配置。启用它们或使用本地构建镜像时，参阅[工作空间自动配置](configuration.md#workspace-provisioning)。`all` 角色在创建工作空间和启动时补建缺失的默认资源。失败时，修复 Engine 访问并重启。成功配置后会保留后续模板修改、禁用和删除结果。自动配置创建的是配置资源；实际实例在收到请求后分配。

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

更新模板时，将模板 ETag 放入 `If-Match`。Recipe 在首次认领创建任务时固定，应在此之前修改，或创建新实例。空闲策略立即适用于已有实例。禁用模板会阻止新预留，也会阻止尚处于预留状态的实例首次创建；已经创建的实例继续可用。

### 默认环境变量

在模板的**环境变量**中添加普通命令默认值，例如 `APP_MODE=agent`。API 位置为 `config.recipe.environment`：

```json
{"environment":{"APP_MODE":"agent","TOOL_ENDPOINT":"https://tools.example.com"}}
```

这是 recipe 片段。命令及其子进程接收这些值。[执行策略](agents-and-runs.md#execution-policy)和命令显式指定的值可以覆盖默认值，而不修改共享实例。Local 还要求变量名列入 `allowed_environment_keys`，除非该白名单不设限制。

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

失败信息包含代码、消息和操作结果是否确定。Service 使用原有操作标识核对未完成操作。出现 `provider_credential_changed` 时，恢复实例原账号的访问。出现 `environment_lost` 等永久失败时，尽可能修复原因，或删除失败实例并新建。

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

重试和继续执行保留已接受的选择。分叉默认共享挂载，除非选择 `fresh_environments`；归档会移除线程挂载。异步[子 agent](agents-and-runs.md#subagents) 使用调用边的 `shared`、`dedicated` 或无环境策略。内联子 agent 共享父运行环境。

### 复用实例并选择项目目录

新会话的**选项**中选择**复用现有实例**，可填写**工作目录**。已有会话使用**环境与记忆 → 添加环境**。留空即使用 provider 默认目录。

选择环境内已经存在的路径。Local 的 `/projects/app` 表示 `{模板根目录}/{环境 ID}/projects/app`，不是浏览器所在计算机的路径。Service 在激活时检查目录；目录不存在或不可访问时报告错误，不会创建目录。对应 API 字段是创建挂载时的 `working_directory`。

相对文件、挂载路由和命令 cwd 都使用该目录。两个线程可以在同一实例的不同目录组织工作，但仍共享进程、端口、已安装软件和操作系统权限。任务需要隔离时，选择提供隔离的后端，并为每个任务使用独立实例。

## Computer Use

桌面任务需要三个条件：兼容的图形环境、agent 已启用的**电脑**工具集，以及线程挂载的电脑权限。先准备桌面，再允许观察或交互。

### 创建 Docker 桌面

1. 配置 Docker provider 的**环境操作通道**和**主机访问地址**。Worker 必须能访问该地址及 Docker 分配的端口；Engine 访问是另一条连接。
2. 创建 Docker 模板，选择**桌面环境**。预设会选择桌面镜像、HTTP 执行方式和桌面就绪要求。
3. 将模板选为 agent 默认环境，或创建实例后添加到会话。
4. 实例运行后，在**环境与记忆**中选择**检查桌面**，再按下文授予权限。

使用回环地址或明确可信的私网 HTTP 路由，并配置对应出站策略。其他主机名需要明确的监听 IP。自定义图形镜像须兼容 Envd HTTP，并设置 `execution: "envd_http"` 和 `desktop: true`。人工桌面控制还需要浏览器可达的查看路由，见下文。

### 自动创建 Vercel 桌面

1. 使用 access token 添加 Vercel provider，选择**加载 Vercel 团队**，再选择该 token 可以访问的团队和项目。
2. 创建模板，选择**桌面环境 → 自动桌面**。
3. 创建实例。首次构建可能需要数分钟，Console 显示阶段和已等待时间。离开页面后，可以回到**环境实例**检查进度。
4. 就绪后检查桌面预览，再启用 agent 权限。

Token 需要沙箱和快照的创建、读取、删除权限，并有适合无人值守使用的有效期。后续创建会在同一工作空间、provider、凭据版本、团队、项目和构建版本内复用已验证快照。临时构建实例会被移除，可复用快照保留在 Vercel 项目中。

### 启用观察和操作

1. 启用 agent 的**电脑**工具集并保存。单个工具的审批仍然生效。
2. 在**环境与记忆**中打开挂载的**电脑操作权限**。允许观察可获取截图，允许交互可使用鼠标和键盘。
3. 发送新消息，要求 agent 描述屏幕。展开其工作步骤，在观察步骤选择**查看截图**。
4. 执行交互任务时，检查下一张截图，确认应用发生了预期变化。

电脑权限默认关闭。修改对新接收的运行生效，`workspace` 也遵循此规则。桌面就绪状态与权限独立：**检查桌面**只探测运行中的实例，不截图、不发送输入、不授予权限，也不启动已停止实例。已保存的步骤截图是历史画面；实时查看使用桌面控制。

API 客户端通过 `PATCH /api/v1/threads/{thread_id}/environments/{name}` 更新挂载，并携带线程 ETag。以下请求体只允许电脑观察：

```json
{"permission_ceiling":{"operations":["environment.computer.describe","environment.computer.observe","environment.computer.zoom"]}}
```

动作列表会替换整个权限集合。请包含需要保留的文件和执行动作；Console 预设会保留它们。电脑权限控制 Computer 工具，Shell 和其他工具仍使用各自的权限。

### Computer 坐标模式

模型返回相对位置时，添加以下 agent 配置片段：

```json
{"toolsets":{"computer":{"enabled":true,"config":{"coordinate_space":"normalized_1000"}}}}
```

直接传入 0 到 1000 的位置。工具将其转换为截图像素，包括拖拽的两个端点。返回像素的模型使用默认 `pixels` 模式。滚动距离和键盘输入保持不变。每次操作后都检查应用结果。

### 桌面访问前提

在 recipe 中启用 `desktop: true` 和 `desktop_assistance: true`。这些设置准备人工查看和 Computer 控制，不会添加 agent 工具。Service 不提供内置桌面协助请求工具；应用可以自行提供请求流程。agent 的 Computer 工具和挂载权限需单独配置。按 [provider 指南](../environments/providers.md)准备兼容的桌面访问 v2 端点。

Docker 需要配置 `desktop_viewer_url`，例如 `wss://desktop.example/{container_id}/websockify`，并路由到各容器的 6081 端口。Engine 和 Envd 访问不能提供这条浏览器路由。只有回环地址接受明文 WS。E2B 需要自动准备或兼容的自定义桌面模板，仅有普通 `desktop` 模板还不够。其他 provider 使用已准备的 supervisor 及各自的路由。打开查看器不会启动环境或延长环境寿命。

### 获取与归还桌面控制权

1. 在可交互会话中打开**桌面**，选择环境挂载。
2. 选择**查看桌面**进行只读查看，或选择**接管**申请 Computer 输入控制。Service 等待已接收的 Computer 操作结束。
3. 出现**你正在控制 Computer**后，选择**连接输入**。文件、Shell、进程、脚本、其他桌面和 agent 的模型工作继续运行。
4. 需要更多时间时选择**续期控制权**。默认租约为五分钟；运维者通过 `control.desktop_control_seconds`、`control.desktop_control_min_seconds` 和 `control.desktop_control_max_seconds` 配置默认值和请求范围。
5. 完成后选择**归还控制权**。Service 撤销输入并确认按键和鼠标按钮清理，再重新开放 Computer 通道。

关闭查看器或页面只会断开该查看器，控制权保持到显式归还或租约过期。短期连接授权与人工租约独立续期。同一原生桌面同时只有一个主体控制，即使多个环境别名或 thread 共享它。只读查看器保持独立。Browser 控制有自己的生命周期。

接管期间受阻的 Computer 调用返回未执行结果，并为该 thread 登记交还通知。agent 可以继续其他工作或结束当前回合，无需轮询。确认交还后，现有收件箱向仍符合条件的调用方投递可用性提示。agent 必须观察当前桌面并进行新的模型决策，之后才能发送输入；旧手势不会重放。已结束的子 agent 不会重新启动。待处理问题和审批仍需按原有流程提交完整响应批次。

Computer 影响不确定或输入清理未确认时，Computer 访问保持阻塞，等待核对。该控制边界覆盖受管理的 Computer 调用，包括 CodeAct 内的调用。它不会冻结任意访客进程、Shell 启动的界面自动化或独立客户端。

### 升级桌面控制

此变更用环境级 Computer 控制替代运行级协助和暂停/恢复 API。请协调切换：

1. 在旧版本停止接收新任务和接管请求，等待活跃 worker 和保留工具执行结束。
2. 使用旧版本撤销并核对所有旧桌面授权和预留。完成并恢复旧桌面等待，或取消其当前 thread 头。保留已完成的历史运行。先解决清理结果不确定的问题，再继续。
3. 停止全部旧 control 和 worker 副本。备份数据库，通过受支持的迁移流程执行一次 schema 迁移。仍有活跃预留、未解决协助或可恢复的桌面等待时，迁移会拒绝执行。
4. 同步升级 control、worker 和客户端。启用人工访问前，将桌面访客环境准备为协议 v2。现有 v1 环境需要显式替换为兼容环境或由运维升级；连接操作不会升级环境。
5. 在已准备的目标上验证查看、接管和归还，再重新开放请求。

此切换不支持新旧副本混合运行。出现旧版本无法表达的新控制或交还历史后，数据库会拒绝降级。此时应向前修复。

### 自动创建 Sprites 桌面

1. 添加 Sprites provider，填写组织和允许归属标签、执行、删除、URL 设置的 token。
2. 创建模板并启用**桌面环境**。
3. 创建实例。每个新 Sprite 都安装 Envd、桌面依赖和 Chrome。Console 显示进度，可以稍后返回检查，或在确认失败后重试。
4. 就绪后，选择**查看桌面**获取截图。交互访问使用会话中的桌面控制。

已有 Sprite 在休眠后保留文件和安装结果，新 Sprite 则重新安装。安装失败会保留所属目标，供显式重试或删除。Sprites 不支持显式停止。浏览器访问使用短期授权和 VNC 认证，云 API Token 保留在 Service。
