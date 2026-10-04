---
title: Service 快速入门
sidebarTitle: 快速入门
description: 使用 Docker Compose 在本地运行 Service，连接模型并获得 agent 的首次回复。
---

你需要 Docker、Docker Compose 和模型 provider 的 API 密钥。无需 Python、Node.js 或源码构建。模型调用使用你的 provider 账号，可能产生费用。

> [!TIP]
> 已经有运行中的 Service？可以[使用团队的平台](use-platform.md)，或[连接你的应用](connect-application.md)。

## 使用 Docker Compose 启动

将 [`a13n-service.yaml`](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service.yaml) 下载到空目录中，再启动整套服务。可以直接复制以下命令，无需克隆仓库或安装 Make：

```sh
mkdir a13n-service
cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
```

Service、Console、PostgreSQL 和 Redis 会一同启动。这套配置挂载宿主机的 Docker socket，并自动为每个工作空间添加一个 Docker 环境 provider 和一个 `Linux Sandbox` 环境模板；对外只开放 <http://127.0.0.1:8080>。仓库中的配置使用已发布的 `latest` 镜像，release 附件中的配置则固定到对应的发布版本。

如果已经克隆仓库，在仓库根目录运行 `make compose-up` 也会启动同一套服务，并输出 Console 的 URL。

## 注册管理员账号

打开 <http://127.0.0.1:8080>。**首次启动时，请先注册管理员账号：** 输入邮箱，并设置至少 **8 个字符** 的密码。这套配置没有默认的管理员邮箱或密码。

注册会创建首个管理员、组织和工作空间，并自动登录。接下来[添加模型](#add-a-model)。之后访问时，使用注册的邮箱和密码登录。重启会保留账号和数据；其他用户通过[邀请](identity.md#invitations)加入。

在尚未初始化的 Service 中，第一个注册的人会成为管理员。设置期间请保持服务仅监听回环地址；如果其他人能在你打开 Console 之前访问新部署，请先使用[运维命令初始化](#initialize-a-shared-deployment)。[Compose 指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker)介绍了宿主机 Docker 访问权限和部署配置。

## 添加模型

1. 打开 **模型 → 添加模型 → 接入新的供应商**。
2. 选择 provider 类型，例如 OpenAI 或 Anthropic。
3. 输入它的 API 密钥。
4. 选择 **接入供应商**，再从目录中选择模型，或选择 **自定义模型** 并输入上游模型 ID。
5. 为模型命名，检查上游 ID 和 API，然后选择 **添加模型**。如果使用 OpenAI 兼容端点，请选择它支持的 API，例如 **OpenAI Chat Completions** 。请选择你的 provider 账号有权使用的模型。

出站请求默认拒绝私有地址和明文 HTTP。要使用自己网络中的模型服务器，需先允许访问，参阅[出站请求](configuration.md#outbound-requests)。所有 provider 类型请参阅[模型](models.md)。

## 创建并试用 agent

1. 打开 **Agents → 手动创建**，命名为 `My first agent`，选择模型，并填写指令，例如 `You are a helpful assistant. Answer clearly and briefly.`。
2. 保存。每次保存都会创建一个不可变的修订版本，在 Console 的 **版本** 中列出。
3. 选择 **试用 Agent**，发送 `Give me three ideas for a useful agent I could build.`。回复应当流式出现在对话中。

首次对话无需配置执行环境、工具或记忆。模型连接成功后再添加这些能力即可。

接下来阅读 [Console 指南](use-platform.md)，了解追问、审批、问题和文件的使用方法。

## 使用 API

按照[连接你的应用](connect-application.md)创建工作空间 API 密钥、选择 agent、提交消息并读取结果。各语言的集成可以选择 [Service SDK 或远程 CLI](sdks.md)。

## 停止、恢复或重置服务

在 `a13n-service.yaml` 所在目录运行以下命令：

```sh
# Stop containers, keeping accounts, conversations, credentials, and files.
docker compose -f a13n-service.yaml down

# Resume with the same data.
docker compose -f a13n-service.yaml up -d --wait
```

请保持相同的 Compose 项目和数据卷。恢复运行时，管理员账号、密码和数据都会保留。要更新镜像，在启动命令中添加 `--pull always`；升级现有部署前，请先阅读 [Compose 升级指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades)。

要**永久删除整套服务的数据**，运行 `docker compose -f a13n-service.yaml down --volumes`。下次启动后，打开 Console 并注册新的管理员账号。

## 故障排查

- **8080 端口已被占用：** 运行 `A13N_PORT=8081 docker compose -f a13n-service.yaml up -d --wait`，然后打开 <http://127.0.0.1:8081>。恢复运行时继续使用该端口。
- **启动无法完成：** 检查 `docker compose -f a13n-service.yaml ps -a` 和 `docker compose -f a13n-service.yaml logs service`。Service 必须完成数据库迁移并就绪，Console 才能使用。
- **Console 显示登录而非注册页面：** Service 已有管理员。请使用注册的账号登录；重启不会重置账号。
- **模型无法回复：** 检查 provider 凭据、上游模型 ID 和 API 选择。目录列出的模型不代表你的 provider 账号有权使用。私有地址或 HTTP 端点还需要显式配置[出站请求设置](configuration.md#outbound-requests)。

## 部署 Service

本地 Compose 服务可以保留现有账号和数据，供你继续使用。用于多人共享时，请先配置公共 URL 和访问权限，再开放 Service，具体步骤见下方部署指南。Service 提供 `a13n-service` Python 包、支持 `linux/amd64` 和 `linux/arm64` 的 `ghcr.io/converge-ai-labs/a13n-service` 镜像，以及 Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`。每个部署都需要公共 URL、PostgreSQL、Redis、共享对象存储和加密密钥，参阅[配置 Service](configuration.md#required-infrastructure)。仓库维护了两份部署指南：

- [使用 Docker Compose 单机部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker)：在一台机器上运行 Service、PostgreSQL、Redis 和 Console，Docker 环境使用宿主机的 Docker Engine。
- [使用 Helm 部署到 Kubernetes](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes)：分别部署 control 和 worker Deployment，并使用迁移 Job；提供本地 kind 集群的 values 配置。

按照任一指南操作，直到 Service 的 `/readyz` 报告就绪。

## 初始化共享部署

任何尚未初始化的部署都可以在浏览器中[注册管理员](#register-your-administrator-account)。如果其他人能在你打开 Console 之前访问新部署，请先使用运维命令 `bootstrap` 创建管理员。

在能读取 Service 配置的环境中运行以下命令（上述部署中是在 Service 容器内）：

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

命令会提示输入密码，并以 JSON 输出新建的组织、工作空间和用户 ID。浏览器请求只接受来自 Service 公共源的请求；公共 URL 为回环地址时，`localhost` 和 `127.0.0.1` 可以互换。接下来[添加模型](#add-a-model)。
