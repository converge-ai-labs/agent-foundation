---
title: 浏览器
description: 为 Service 智能体挂载云端或容器化托管 Chromium 浏览器，在 Console 中实时查看画面并支持人工接管登录。
---

当任务需要浏览网页、执行 JavaScript、填写表单、截图或人工接管登录时，为智能体绑定 `browser`。浏览器独立于[运行环境](environments.md)管理。

## 选择浏览器后端

| 方案                                                                 | 停止/重启保留登录态    | Console 实时画面与人工接管 | 文件上传/下载           |
| -------------------------------------------------------------------- | ---------------------- | -------------------------- | ----------------------- |
| **Kubernetes**（`ghcr.io/converge-ai-labs/a13n-browser`）            | 支持（停止时保留 PVC） | 支持                       | 不支持                  |
| **Docker** / **Native**（Linux Service 宿主机）                      | 支持                   | 支持                       | 支持（`browser_files`） |
| **云提供方**（`browserbase`、`kernel`、`browser_use`、`cloudflare`） | 不支持（临时会话）     | 不支持                     | 不支持                  |
| **外部 CDP**（浏览器级 `ws://` 或 `wss://` 调试端点）                | 由外部自行管理         | 不支持                     | 不支持                  |

## Console 快速开始

1. 在智能体版本上启用 `browser` 工具集，并配置[工具权限](agent-configuration.md#tool-permissions)。
2. 在 **Providers → Browser providers** 添加提供方账号并在 **Browsers** 创建浏览器（或直接在 **Browsers** 接入外部 CDP 地址）。
3. 打开线程的 **Browser** 面板绑定该浏览器，选择标签页范围（`thread` 仅限本线程创建的标签页，`global` 共享全部标签页）后保存。
4. 对于 Kubernetes、Docker 或 Native 浏览器，随时点击 **Take control** 在实时画面中人工完成登录，结束后点击 **Return control**（**Hand back to agent**）交回控制权。

## 配置自托管与云提供方

### Kubernetes

先按 [Helm 指南](configuration.md#kubernetes-browsers)启用 Kubernetes 浏览器，随后在 Console 创建 Kubernetes 浏览器提供方（`cluster: "in-cluster"`，填入 Helm 输出的命名空间）。保持 CDP 与 VNC 仅集群内私网可达，并通过 `NetworkPolicy` 限制浏览器出站访问。

### Docker 与 Native

在 `control` 与 `worker` 共享目录及本地 Docker Engine 的 Linux 主机上配置 `service.toml`（容器化部署 Service 时需使用主机网络）：

```toml
[browsers]
root = "/var/lib/a13n/browsers"
docker_host = "unix:///var/run/docker.sock"
```

通过 `POST /api/v1/browser-providers` 创建提供方（支持 `docker`、`browserbase`、`kernel`、`browser_use`、`cloudflare`，传入对应 `config` 与 `credential.api_key`）。

## 通过 API 创建、接入与绑定

1. **创建托管浏览器：** 调用 `POST /api/v1/browsers` 传入 `{"provider_id": "...", "name": "Ops browser", "recipe": {}}`（或通过 `POST /api/v1/browsers/attach` 接入外部浏览器级 `ws(s)://` 端点）。
2. **绑定到线程：** 在 `POST /api/v1/threads` 或 `PUT /api/v1/threads/{thread_id}/browser`（携带线程 `If-Match`；传 `null` 解绑）中设置 `browser_binding`：

```json
{
  "browser_id": "browser_0123456789abcdef01234567",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

- **生命周期（需携带浏览器 `If-Match`）：** `POST /api/v1/browsers/{id}/prepare`、`/stop`（暂停自托管实例并保留配置目录）与 `/release`（销毁托管浏览器或移除外部 CDP 注册）。空闲回收使用 `stop_after_seconds` / `delete_after_seconds`（自托管）或 `idle_seconds`（云端默认 `1800`）。
- **API 人工接管流程：** `POST /api/v1/browsers/{id}/takeover` → `/viewer` → `/renew` → `/handback`（租约范围由 `service.toml` 中 `[control] browser_control_*` 控制）。
