---
title: Service 快速入门
sidebarTitle: 快速入门
description: 使用 Docker Compose 在本地运行 Service，连接模型并获得 agent 的首次回复。
---

你需要 Docker、Docker Compose 和模型 provider 的 API 密钥。无需 Python、Node.js 或源码构建。模型调用使用你的 provider 账号，可能产生费用。

> [!TIP]
> 已经有运行中的 Service？可以[使用团队的平台](use-platform.md)，或[连接你的应用](connect-application.md)。

## 使用 Docker Compose 本地试用

通过 GitHub 的 **Download raw file** 按钮下载 [`a13n-service-quickstart.yaml`](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service-quickstart.yaml)。将它保存到空目录中，在该目录打开终端并启动：

```sh
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

打开 <http://127.0.0.1:8080>，使用 **`admin@example.com` / `local-public-password-123`** 登录。管理员、组织和工作空间已预先创建。继续[添加模型](#add-a-model)；要获得实际回复，需要提供你自己的模型 provider 凭据。

> [!WARNING]
> **仅在自己的机器上试用。** 管理员密码是公开的。此配置仅绑定回环地址，不挂载宿主机 Docker socket，并在重启后保留数据和凭据。[Compose 指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#local-quickstart)详细介绍了这套服务。

## 添加模型

1. 打开 **Models → Add model → Connect a new provider**，选择 provider 类型（例如 OpenAI 或 Anthropic），然后输入 API 密钥。
2. 选择 **Connect provider**，再从目录中选择模型，或选择 **Custom model** 并输入上游模型 ID。
3. 为模型命名，检查上游 ID 和 API，然后选择 **Add model**。如果使用 OpenAI 兼容端点，请选择它支持的 API，例如 **OpenAI Chat Completions** 。请选择你的 provider 账号有权使用的模型。

出站请求默认拒绝私有地址和明文 HTTP。要使用自己网络中的模型服务器，需先允许访问，参阅[出站请求](configuration.md#outbound-requests)。所有 provider 类型请参阅[模型](models.md)。

## 创建并试用 agent

1. 打开 **Agents → Create agent**，命名为 `My first agent`，选择模型，并填写指令，例如 `You are a helpful assistant. Answer clearly and briefly.`。
2. 保存。每次保存都会创建一个不可变版本。
3. 选择 **Try agent**，发送 `Give me three ideas for a useful agent I could build.`。回复应当流式出现在对话中。

首次对话无需配置执行环境、工具或记忆。模型连接成功后再添加这些能力即可。

接下来阅读 [Console 指南](use-platform.md)，了解追问、审批、问题和文件的使用方法。

## 使用 API

按照[连接你的应用](connect-application.md)创建工作空间 API 密钥、选择 agent、提交消息并读取结果。各语言的集成可以选择 [Service SDK 或远程 CLI](sdks.md)。

## 停止、恢复或重置试用

在快速入门文件所在目录运行以下命令：

```sh
# Stop containers, keeping accounts, conversations, credentials, and files.
docker compose -f a13n-service-quickstart.yaml down

# Resume with the same data.
docker compose -f a13n-service-quickstart.yaml up -d --wait
```

请保持相同的 Compose 项目和数据卷。初始化可以安全重复运行；如果修改过密码，它不会恢复公开密码。

要**永久删除所有试用数据**，运行 `docker compose -f a13n-service-quickstart.yaml down --volumes`。下次启动时会重新创建公开的试用账号。

## 故障排查

- **8080 端口已被占用：** 运行 `A13N_PORT=8081 docker compose -f a13n-service-quickstart.yaml up -d --wait`，然后打开 <http://127.0.0.1:8081>。恢复运行时继续使用该端口。
- **启动无法完成：** 检查 `docker compose -f a13n-service-quickstart.yaml ps -a` 和 `docker compose -f a13n-service-quickstart.yaml logs init service`。初始化程序必须成功完成，Service 才能启动。
- **试用密码无法登录：** 现有数据会被保留，包括修改后的密码。请使用你设置的密码登录；重启不会重置密码。
- **模型无法回复：** 检查 provider 凭据、上游模型 ID 和 API 选择。目录列出的模型不代表你的 provider 账号有权使用。私有地址或 HTTP 端点还需要显式配置[出站请求设置](configuration.md#outbound-requests)。

## 部署 Service

正式部署时请使用自己的管理员凭据。Service 提供 `a13n-service` Python 包、支持 `linux/amd64` 和 `linux/arm64` 的 `ghcr.io/converge-ai-labs/a13n-service` 镜像，以及 Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`。每个部署都需要 PostgreSQL、Redis 和加密密钥，参阅[配置 Service](configuration.md#required-infrastructure)。仓库维护了两份部署指南：

- [使用 Docker Compose 单机部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker)：在一台机器上运行 Service、PostgreSQL、Redis 和 Console，Docker 环境使用宿主机的 Docker Engine。
- [使用 Helm 部署到 Kubernetes](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes)：分别部署 control 和 worker Deployment，并使用迁移 Job；提供本地 kind 集群的 values 配置。

按照任一指南操作，直到 Service 的 `/readyz` 报告就绪。

## 创建首个管理员

此步骤适用于尚未初始化的部署，而不是上面已初始化的本地试用。通过 Service 的公共 URL 打开 Console。初始化前，Console 会要求填写首个管理员的邮箱和至少 12 个字符的密码，创建后自动登录。此操作只创建一次首个组织、工作空间和管理员；此后 Console 显示登录界面，其他用户通过[邀请](identity.md#invitations)加入。

第一个访问未初始化 Service 的人会成为管理员。如果其他人能在你打开新部署之前访问它，请在能读取 Service 配置的环境中使用运维命令 `bootstrap` 创建管理员（上述部署中是在 Service 容器内）：

```sh
a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

命令会提示输入密码，并以 JSON 输出新建的组织、工作空间和用户 ID。浏览器请求只接受来自 Service 公共源的请求；公共 URL 为回环地址时，`localhost` 和 `127.0.0.1` 可以互换。接下来[添加模型](#add-a-model)。
