---
title: Service 快速入门
sidebarTitle: 快速入门
description: 使用 Docker Compose 启动 Service，并获得第一个 agent 的回复。
---

本指南从一个空目录开始，带你在 Console 中完成一次 agent 对话。请准备好 Docker、Docker Compose、`curl` 和模型 provider 的 API 密钥。整套服务使用已发布的镜像，无需 Python、Node.js 或源码构建。模型调用使用你的 provider 账号，可能产生费用。

已经能访问 Service？请从[使用 Console](use-platform.md)或[连接你的应用](connect-application.md)开始。

## 使用 Docker Compose 启动

在终端运行以下命令。它们会创建目录、下载 Compose 文件，并等待整套服务通过健康检查：

```sh
mkdir a13n-service
cd a13n-service
curl -fL https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/deploy/docker/compose/a13n-service.yaml -o a13n-service.yaml
docker compose -f a13n-service.yaml up -d --wait --pull always
curl -fsS http://127.0.0.1:8080/readyz
```

就绪响应应包含 `"status":"ready"`。打开 <http://127.0.0.1:8080> 使用 Console。如果启动失败，先按[故障排查](#troubleshooting)处理，再继续。

这套配置运行 Service、Console、PostgreSQL 和 Redis，只开放上述回环地址。它挂载宿主机 Docker socket，因此 Service 拥有宿主机 Docker 权限。Engine 可访问时，Service 会为每个工作空间准备 Docker provider 和 `Linux Sandbox` 环境模板。第一次纯模型对话不需要沙箱。

[仓库中的 Compose 文件](https://github.com/converge-ai-labs/agent-foundation/blob/main/deploy/docker/compose/a13n-service.yaml)选择已发布的 `latest` 镜像；release 附件则固定到对应版本。如果已克隆仓库，在仓库根目录运行 `make compose-up` 会启动同一套服务，并输出 Console URL。

## 注册管理员账号

1. 打开 Console，用邮箱和至少 **8 个字符** 的密码注册。没有默认管理员账号。
2. 自动登录后，在新建的组织和工作空间中继续操作。
3. 保存登录凭据，以便下次访问。重启会保留账号和数据；其他用户通过[邀请](identity.md#invitations)加入。

首个注册的人会成为管理员。设置本地服务期间，请保持仅监听回环地址。如果其他人能在你打开 Console 之前访问新部署，请先[使用运维命令初始化](#initialize-a-shared-deployment)，再对外开放。

## 添加模型

1. 打开**模型 → 添加模型**。尚未连接 provider 时，会直接显示连接表单；否则，选择**接入新的供应商**。
2. 选择 provider 类型，例如 OpenAI 或 Anthropic，输入 API 密钥，再选择**接入供应商**。
3. 从目录选择模型，或选择**自定义模型**并输入上游模型 ID。
4. 为模型命名，检查上游 ID 和 API，再选择**添加模型**。对于 OpenAI 兼容服务器，选择它支持的 API，例如 **OpenAI Chat Completions**。

请选择你的 provider 账号有权使用的模型。出站请求默认要求 HTTPS；使用明文 HTTP 服务器前，先在[出站请求设置](configuration.md#outbound-requests)中允许其精确 origin。[模型](models.md)介绍了可用的 provider 类型。

## 创建并试用 agent

1. 打开 **Agents → 手动创建**。
2. 命名为 `Project notes`，选择模型，并填写指令：`Turn project notes into two concrete next steps. Keep the answer brief.`。
3. 选择**创建 Agent**。Console 会将初始配置保存为不可变的修订版本，在**版本**中列出。
4. 选择**试用 Agent**，发送：`Draft ready; review pending; release planned for Friday. What should I do next?`。
5. 等待回复，再追问：`Make the first step more specific.`。

现在，你有了已保存的 agent，以及保留讨论历史的线程。具体措辞取决于模型；请检查回复是否围绕笔记展开，追问是否使用了前文。本流程只需要模型和指令。任务需要时，再添加工具、环境和记忆。

接下来阅读[使用 Console](use-platform.md)，了解如何返回线程、创建分支，以及处理问题或审批。

## 使用 API

打开 agent 的**接入应用**操作，获取其 ID 和你的 Service URL。[连接你的应用](connect-application.md)介绍了凭据、第一条消息和结果读取流程。各语言的集成可以使用 [SDK 或远程 CLI](sdks.md)。

## 停止、恢复或重置服务

在 `a13n-service.yaml` 所在目录运行：

```sh
# Stop containers and keep the data volumes.
docker compose -f a13n-service.yaml down

# Start again with the same accounts, conversations, credentials, and files.
docker compose -f a13n-service.yaml up -d --wait
```

保持相同的 Compose 项目和数据卷。升级时，先按照[备份和升级流程](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#backups-and-upgrades)操作，再在启动命令中添加 `--pull always` 更新镜像。

重置前，先通过 Service [删除托管环境](environments.md#stop-rename-and-delete)。这些容器位于 Compose 服务之外，Service 需要保留的记录来清理它们。然后运行 `docker compose -f a13n-service.yaml down --volumes`，**永久删除整套服务的数据**。下次启动需要重新注册管理员。

## 故障排查

| 现象                                | 处理方法                                                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 8080 端口被占用                     | 使用 `A13N_PORT=8081 docker compose -f a13n-service.yaml up -d --wait` 启动，再打开 <http://127.0.0.1:8081>。之后启动时继续使用该端口设置。 |
| 启动无法完成                        | 检查 `docker compose -f a13n-service.yaml ps -a` 和 `docker compose -f a13n-service.yaml logs service`。Service 必须完成迁移并就绪。        |
| 就绪响应包含 `"degraded":["redis"]` | 检查 Redis 容器和连接。Service 仍然就绪，但 Redis 提供的加速功能已降级。                                                                    |
| Console 要求登录                    | 部署已经初始化。使用已注册的账号；重启不会重置它。                                                                                          |
| 模型无法回复                        | 检查 provider 密钥、上游模型 ID、所选 API 和账号权限。明文 HTTP 端点还需要显式[允许其 origin](configuration.md#outbound-requests)。         |

## 部署 Service

共享部署应在对外开放前配置公共 URL 和访问权限。按照[必需基础设施](configuration.md#required-infrastructure)准备 PostgreSQL、Redis、共享对象存储和加密密钥。

选择部署指南：

- [使用 Docker Compose 单机部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose#single-host-deployment-with-native-docker)：在一台机器上运行整套服务，并使用其 Docker Engine 创建环境。
- [使用 Helm 部署到 Kubernetes](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes)：分别运行 control 和 worker Deployment，使用迁移 Job。指南包含本地 kind 集群的 values 配置。

Service 提供 `a13n-service` Python 包、支持 `linux/amd64` 和 `linux/arm64` 的 `ghcr.io/converge-ai-labs/a13n-service` 镜像，以及 Helm Chart `oci://ghcr.io/converge-ai-labs/charts/a13n-service`。按照所选指南操作，直到 `/readyz` 报告就绪。

## 初始化共享部署

在其他人能访问尚未初始化的部署之前，使用 `bootstrap` 创建管理员。对于上面的 Compose 服务，在 Service 启动后，从 YAML 文件所在目录运行：

```sh
docker compose -f a13n-service.yaml exec service \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
docker compose -f a13n-service.yaml restart service
```

将 `admin@example.com` 替换为你的邮箱。命令会提示输入密码，并以 JSON 输出组织、工作空间和用户 ID。重启会为 CLI bootstrap 创建的工作空间准备默认资源。其他部署方式中，在已配置的 Service 容器内运行同一条 `a13n-service` 命令，并通过相应部署工具安排下次启动。

在配置的公共 URL 登录，然后[添加模型](#add-a-model)。浏览器身份验证请求使用 Service 的公共源；公共 URL 为回环地址时，`localhost` 和 `127.0.0.1` 可以互换。
