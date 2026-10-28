---
title: 文件、命令与操作限制
sidebarTitle: 文件、路径与限制
description: 选择环境操作、定位目标文件并读取有界结果。
---

使用环境操作，直接从 Python 读写文件、运行命令和观测进程。先运行[快速入门](getting-started.md)中的完整文件示例，再用本页选择方法并理解返回结果。

## 操作类别

打开 connector 后，在 `execution.operations` 下选择任务所需的接口。每个接口都是可选的；执行对象的描述符列出支持的操作族和精确操作。

| 类别      | 用于                             | 使用结果前需检查                       |
| --------- | -------------------------------- | -------------------------------------- |
| Files     | 读取、写入、列出、搜索和移动文件 | provider 的路径规则和访问限制          |
| Shell     | 运行命令并返回有界前台输出       | 获准可执行文件、shell 语法和支持的限制 |
| Processes | 启动命令，跨调用观测或控制进程   | 各项操作的支持情况，包括发现和信号     |
| Outputs   | 按偏移量读取保留的 stdout/stderr | 可用范围和观测是否完整                 |
| Ports     | 观测受支持的目标是否正在监听     | 配置的地址和端口允许列表               |

connector 的 `open()` 返回执行对象时，所声明的操作族已就绪。之后调用 `check_ready()` 进行只读检查。某个操作族故障时，其他操作族仍可能可用；就绪检查不会修复目标。

Native Files & Shell 覆盖文件、命令、进程和输出操作。Envd 提供端口观测等额外能力。Direct Local 也可通过 `allowed_ports` 显式启用 loopback 端口观测，见[端口与生命周期](commands.md#ports-and-lifecycle)。

## 路径遵循所调用边界的规则

按所调用的 API 选择路径格式：

| provider            | 文件路径示例                                                   | 含义                        |
| ------------------- | -------------------------------------------------------------- | --------------------------- |
| Native Local 或 E2B | `/hello.txt`                                                   | 配置根目录内的路径          |
| Native Docker       | `/workspace/hello.txt`                                         | 容器路径                    |
| Envd                | `/home/user/hello.txt`                                         | 设备绝对 POSIX 路径         |
| Windows 上的 Envd   | `/C:/Users/example/hello.txt` 或 `/UNC/server/share/hello.txt` | 设备绝对盘符路径或 UNC 路径 |

Harness 在这些 provider API 之上添加挂载选择和相对路径解析。向底层文件操作接口传入 provider 本地路径；Harness 聚合路径只有同时符合该 provider 的规则时，才能直接使用。

映射根目录用于定位文件，不会在 OS 层面隔离获准命令。Envd 的固定工作目录同样不限定文件访问；访问受设备启动 Sandbox 和出站网络模式约束，并处于外层 Host 授予的权限范围内。

## 环境操作与工具

应用自行执行操作时，使用带类型的 provider 接口。要让 agent 执行操作，请按[在 Harness 中使用环境](../a13n-harness/environments.md)配置执行输入和 `DynamicEnvironmentCapability`。

Harness 提供挂载名称、路由、访问上限、操作超时和状态聚合。模型工具需要额外配置：添加环境本身不会暴露工具。provider 仍负责实施其声明支持的操作。

## 文件操作参考

打开执行对象后，获取 `execution.operations.files` 并检查接口可用，再按任务选择方法：

| 方法                                                                   | 输入与结果                                               |
| ---------------------------------------------------------------------- | -------------------------------------------------------- |
| `read_text(path, line_offset=0, line_limit=200, max_line_length=2000)` | 文本、`lines_read`、`has_more` 和行截断证据              |
| `read_bytes(path, offset=0, length=None)`                              | provider 值大小限制内的字节                              |
| `read_bytes_stream(path, chunk_size=65536)`                            | 二进制传输异步迭代器；迭代时不要对迭代器本身调用 `await` |
| `write_bytes_stream(path, stream, mode=...)`                           | 异步字节迭代器、显式写入模式和 `FileWriteResult`         |
| `write_text(path, text, mode=...)`                                     | 显式写入模式、写入字节数和修改回执                       |
| `write_archive(path, FileArchive(...))`                                | 将 ZIP 清单中的文件解压到不存在的目录                    |
| `batch(operations)`                                                    | 按顺序执行文件操作，结果顺序一致                         |
| `patch_text(path, patch)`                                              | 文本补丁和已应用 hunk 回执                               |
| `stat(path)`                                                           | 类型、可选大小和是否可写                                 |
| `list(path, max_results=..., offset=0, include_hidden=False)`          | 浅层条目和 `has_more`                                    |
| `query(FileQueryRequest(...))`                                         | 按模式选择的元数据                                       |
| `search_text(FileTextSearchRequest(...))`                              | 文本匹配，行号和上下文位置从 1 开始                      |
| `mkdir(path, parents=False, exist_ok=False)`                           | 显式父目录/已存在时的行为                                |
| `move(source, destination, replace=False)`                             | 同一环境内移动                                           |
| `copy(source, destination, replace=False)`                             | 同一环境内复制，报告复制字节数                           |
| `remove(path, recursive=False)`                                        | 显式递归删除策略                                         |

写入时，新文件选 `create`，已有文件选 `replace`，两者皆可选 `upsert`，追加选 `append`。必须指定模式。`copy` 和 `move` 在同一环境内操作；跨挂载复制由 Host 或聚合层负责。

执行一小组修改时，向 `batch` 传入 `FileStatOperation`、`FileMkdirOperation`、`FileWriteTextOperation` 或 `FileRemoveOperation`。同一文件作用域内，每批最多接受 128 项操作和 64 KiB JSON。批量写入支持 `create`、`replace` 和 `upsert`；追加使用单独调用。stat 和 remove 支持 `missing_ok=True`。执行遇到首个错误即停止，已完成的操作保留。先检查这些效果，再决定下一步：失败或取消都不会触发回滚或重放。

写入一组目录文件时，用相对路径和解压后大小构造 `FileArchive(content=zip_bytes, entries=(("SKILL.md", size),), root="")`。可选 `root` 选择 ZIP 内的前缀。先通过 `write_archive` 解压到新的候选目录，再用单独的 `move` 发布完整候选目录。解压保留普通文件内容，不恢复归档链接或权限。自定义 `FileOperator` 必须提供 `batch` 和 `write_archive`。

进行有界读取和搜索时：

1. 设置必需的结果上限：`FileQueryRequest` 使用 `max_results`，`FileTextSearchRequest` 使用 `max_matches`。两种请求还都要求 `root` 和 `pattern`。
2. 选择发现过滤条件。查询默认递归；两种请求默认排除隐藏条目，并使用 `ignore_mode="none"`。选 `"git"` 可应用仓库忽略规则。文本搜索默认使用 `include="**/*"`。
3. 读取 `has_more`。按返回条目或匹配数量推进请求偏移量；读取文本则按 `lines_read` 推进。分页期间保持过滤条件和文件系统稳定。
4. 确认截断证据后，再判断文本是否完整。文本搜索默认文件上限为 64 MiB，行长上限为 2,000 字符；上下文最多 20 行。可选 `max_matches_per_file` 和 `max_files` 必须为正数。

命令执行见[运行命令与观测进程](commands.md)。

## 文件搜索模式

Native Local、Docker、E2B 和 Envd 的文件查询与文本搜索 `include` 过滤使用相同路径语法：

| 模式                       | 选择范围                         |
| -------------------------- | -------------------------------- |
| `*.py`                     | 每个遍历深度的 Python 文件名     |
| `/*.py`                    | 所选根目录直接下属的 Python 文件 |
| `src/*.py`                 | `src` 直接下属的 Python 文件     |
| `{src,tests}/**/*.{py,rs}` | 两个目录下的 Python 或 Rust 文件 |

递归使用完整 `**` 路径段。花括号组接受两个或更多非空选项，不能嵌套，最多展开为 256 个模式。单个模式最多 16 KiB。反斜杠转义和数字范围不会按 shell 方式展开。

环境 API 的文本搜索默认区分大小写并按字面匹配。Harness 的模型工具 `grep` 默认 `regex=true`；搜索字面代码片段时选 `regex=false`，需要忽略大小写时选 `case_sensitive=false`。可移植正则表达式使用字面量、字符类、分组、选择、锚点和量词。Native Local、Docker 和 E2B 使用 Python `re`；Envd 使用 Rust `regex`，拒绝前后查找和反向引用。引擎专属扩展和 Unicode 边界情况可能不同。

空结果表示成功。无效模式返回 `environment_request_invalid`，包含字段、原因和修正提示。提高限制前，先收窄根目录和 include 过滤。逐文件匹配上限限制该文件返回的匹配数；超过符合条件文件的扫描上限会产生限制错误，不会把未完成的扫描报告为完整结果。

## 等待期限不是命令期限

用等待超时限制应用等待观测结果的时长，用受支持的命令限制在期限后停止执行。例如，E2B 有沙箱 TTL 和 SDK 请求期限，但会在启动前拒绝逐命令 `limits.wall_time_seconds`。

从返回偏移量继续读取输出，并检查可用范围和完整性证据。重复等待不会重置观测预算。需要在该预算之外保留完整输出时，写入有明确保留策略的应用日志文件。

## 恢复与可移植性

将进程句柄保留在其所属执行范围内。重新打开同一目标会创建新的观测范围，旧句柄仍然无效。新适配器只能发现 provider 保留且支持发现的内容。先检查中断命令的结果，再采取后续行动；绝不能自动重启缺失命令。

后端差异见 [provider 专属限制](providers.md#cloud-providers)，模型工具签名和策略见 [Harness 环境工具](../a13n-harness/environments.md)。
