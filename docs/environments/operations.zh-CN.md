---
title: 文件、命令与操作限制
sidebarTitle: 文件、路径与限制
description: 环境提供的结构化文件与命令操作、路径规则和限制。
---

环境操作是无需 agent 即可使用的带类型 Python 接口。可用接口取决于所选 provider 和配置；检查就绪状态和实际描述符，不要从后端名称推断支持范围。

## 操作类别

| 类别      | 用途                                   | 主要边界                                   |
| --------- | -------------------------------------- | ------------------------------------------ |
| Files     | 读写、元数据、列表、查询、搜索和修改   | 逻辑路径及 provider 实施的访问限制         |
| Shell     | 运行一个有界命令并返回结果             | 命令语法、继承、期限和隔离因 provider 而异 |
| Processes | 在支持时检查、等待、写入输入和发送信号 | 发现与控制分别提供支持                     |
| Outputs   | 按显式偏移量读取保留 stdout/stderr     | 观测可能不完整或已被驱逐；不是持久日志     |
| Ports     | 观测支持的目标                         | 不提供公共入口或自动端口共享服务           |

完整文件示例见[快速入门](getting-started.md)。connector 的 `open()` 返回执行对象，其声明的操作族已就绪。之后可用 `check_ready()` 只读检查就绪状态；它不会修复或替换目标。即使其他操作可用，某个接口也可能不存在。

## 路径遵循所调用边界的规则

单环境 API 遵循各 provider 的路径契约。Direct Local 和六个云 provider 使用配置根目录内的路径，例如 `/hello.txt`；Docker 使用原生容器路径。Envd provider 使用设备绝对文件系统路径：`/home/user/hello.txt`、`/C:/Users/example/hello.txt` 或 `/UNC/server/share/hello.txt`。固定工作目录不限制文件访问。

Harness 添加挂载选择和相对路径解析。除非聚合 Harness 路径也是该 provider 的有效路径，否则不要直接传给底层 provider 文件操作接口。根映射不为获准命令提供 OS 隔离。Envd 实施设备的启动 Sandbox 和出站网络模式；外层 Host 边界仍决定可用权限。

## 环境操作与工具

provider 包定义结构化文件、shell、进程、保留输出和端口操作契约。已进入的适配器只声明能够实施的操作类别和精确操作。

Harness 应用挂载名称、访问上限、路由、操作超时、状态聚合和可选模型工具。添加环境不会自动向模型提供工具。执行输入和 `DynamicEnvironmentCapability` 配置见[在 Harness 中使用环境](../a13n-harness/environments.md)。

## 文件操作参考

打开执行对象后，使用其可选 `execution.operations.files` 接口。表中路径遵循所选 provider 契约；Envd 路径为设备绝对路径。

| 方法                                                                   | 输入与结果                                                  |
| ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| `read_text(path, line_offset=0, line_limit=200, max_line_length=2000)` | `FileTextResult`，含文本、已读行数、`has_more` 和行截断证据 |
| `read_bytes(path, offset=0, length=None)`                              | 有界字节，受 provider 值大小限制                            |
| `read_bytes_stream(path, chunk_size=65536)`                            | 二进制传输异步迭代器；不要对迭代器本身调用 `await`          |
| `write_bytes_stream(path, stream, mode=...)`                           | 异步字节迭代器、显式写入模式和 `FileWriteResult`            |
| `write_text(path, text, mode=...)`                                     | 显式写入模式、写入字节数和修改回执                          |
| `patch_text(path, patch)`                                              | 文本补丁和已应用 hunk 回执                                  |
| `stat(path)`                                                           | 类型、可选大小和是否可写                                    |
| `list(path, max_results=..., offset=0, include_hidden=False)`          | 有界浅层条目和 `has_more`                                   |
| `query(FileQueryRequest(...))`                                         | 按模式选择的有界元数据                                      |
| `search_text(FileTextSearchRequest(...))`                              | 有界文本匹配，行号和上下文位置从 1 开始                     |
| `mkdir(path, parents=False, exist_ok=False)`                           | 显式父目录/已存在时的行为                                   |
| `move(source, destination, replace=False)`                             | 同一环境内移动                                              |
| `copy(source, destination, replace=False)`                             | 同一环境内复制，报告复制字节数                              |
| `remove(path, recursive=False)`                                        | 显式递归删除策略                                            |

写入模式为 `create`（新文件）、`replace`（已有文件）、`upsert`（创建或替换）和 `append`，没有隐式默认值。回执报告所属操作结果，不代表多个文件操作组成通用事务。跨挂载复制由 Host/聚合层负责，不能给 `FileOperator.copy()` 虚构第二个别名参数。

`FileQueryRequest` 要求 `root`、`pattern` 和正数 `max_results`；默认递归 true、隐藏 false、忽略模式 `none`、不限类型、偏移量零。需要遵循仓库忽略规则时，显式选择 `ignore_mode="git"`。

`FileTextSearchRequest` 要求 `root`、`pattern` 和正数 `max_matches`。默认字面匹配且区分大小写；`include="**/*"`、隐藏 false、忽略模式 `none`、无上下文、偏移量零。可选 `max_matches_per_file` 和 `max_files` 为正数上限；默认文件大小上限 64 MiB，行长 2,000。上下文最多 20 行。

读取 `has_more`，按返回条目/匹配数量（文本为 `lines_read`）推进输入偏移量。分页期间保持过滤条件和文件系统稳定。末行不完整时需要截断证据；仅凭行数不能证明文本完整。

命令和进程观测见完整[命令示例与类型参考](commands.md)。

## 文件搜索模式

Direct Local、Docker、六个云 provider 和 Envd provider 的文件查询模式与文本搜索 `include` 过滤使用相同路径语法：

| 模式                       | 选择范围                         |
| -------------------------- | -------------------------------- |
| `*.py`                     | 每个遍历深度的 Python 文件名     |
| `/*.py`                    | 所选根目录直接下属的 Python 文件 |
| `src/*.py`                 | `src` 直接下属的 Python 文件     |
| `{src,tests}/**/*.{py,rs}` | 两个目录下的 Python 或 Rust 文件 |

花括号组不能嵌套，至少包含两个非空选项，最多展开为 256 个模式。模式最多 16 KiB。反斜杠转义和数字范围不会按 shell 方式展开。递归匹配使用完整 `**` 路径段。

环境 API 的文本搜索默认字面匹配。面向模型的 Harness `grep` 工具默认 `regex=true`，也提供 `regex=false` 和 `case_sensitive=false`。包含标点的代码片段优先使用字面模式。可移植正则表达式使用字面量、字符类、分组、选择、锚点和量词。Direct Local、Docker 和六个云 provider 使用 Python `re`；Envd 使用 Rust `regex`，拒绝前后查找和反向引用。引擎专属扩展和 Unicode 边界情况可能不同。

无效模式产生 `environment_request_invalid` 错误，携带可安全公开的字段、原因和修正提示。零结果仍为成功。使用返回偏移量继续有界分页，保持过滤条件和文件系统稳定。逐文件匹配上限限制该文件返回的匹配数；符合条件文件的扫描上限触发限制错误，不会悄悄声称结果完整。提高限制前，先收窄根目录和 include 过滤。

## 等待期限不是命令期限

有界等待返回当前已知结果，不一定停止命令。命令期限需要 provider 支持。例如，E2B 有沙箱 TTL 和 SDK 请求期限，但在启动前拒绝逐命令 `limits.wall_time_seconds`。

输出记录报告来源、可用范围、偏移量和观测不完整的信息。后续读取使用返回偏移量。重复检查或等待不重置观测预算。需要完整输出时，将其写入有明确保留策略的应用日志文件。

## 恢复与可移植性

进程引用属于当前适配器/执行观测。复用目标状态不会使旧进程句柄有效。新适配器只能发现其 provider 保留且支持发现的内容；绝不能自动重启缺失命令。

模型工具签名和策略见 [provider 专属限制](providers.md#cloud-providers)和 [Harness 环境工具](../a13n-harness/environments.md)。
