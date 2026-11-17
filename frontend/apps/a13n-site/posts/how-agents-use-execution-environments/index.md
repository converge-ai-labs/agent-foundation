---
title: How agents use execution environments
description: How Harness combines environment context, tools, and execution feedback to help agents work with files, run programs, and complete tasks.
date: '2026-11-17'
authors:
  - name: Yuxuan Chen
    url: https://github.com/YuxuanChen98
    avatar: https://avatars.githubusercontent.com/u/42828324?s=80
---

A user asks an agent to analyze a CSV file, identify monthly sales trends, and produce a chart. In the [previous post](/blog/where-agents-run-their-commands/), we explored where that work can happen: on the local machine, in a sandbox alongside the agent, or in a separate execution environment.

This time, assume the application has selected an environment and placed the CSV in its working directory. How does the agent actually use it?

The model needs to know where the file is and which operations are available. It then needs tools to read and write files, run programs, and retrieve results.

In a13n Harness, three parts work together: context tells the model about the available environments, tools define the permitted operations, and Harness routes tool calls to the selected environment for execution.

## What Harness tells the model

Harness supplies the information needed to act through environment context and tools.

Environment context describes what the current run can access: environment names, path roots, the default environment, permitted operations, and known availability. It is generated from the current set of environments and updated as that set changes.

Tool definitions describe operations and their parameters. Toolsets also carry instructions: how a path selects an environment, how to read a file, and how to wait for a command or inspect its output. File, shell, and desktop tools each provide the guidance relevant to their operations.

For the CSV task, the user or application identifies the input file. Environment context tells the model which environments are available and how to address them. File and command tools provide the operations needed to read the data and run the analysis.

![Context and tools inside Harness connect the model to the selected environment.](context-tools-execution.svg "Figure 1. Harness supplies context and tools, dispatches operations, and returns execution feedback.")

## Why combine shell access with dedicated tools?

One basic choice is whether to give the model only shell access or combine it with dedicated tools.

- **Shell only.** The model uses commands to read files, edit code, and run programs. There are fewer tool interfaces, and existing command-line programs provide considerable flexibility. The model also has to construct commands and handle arguments, escaping, and output formats. Permissions become more complicated: allowing only file reads, for example, requires controls over what shell commands can actually do.
- **Shell plus dedicated tools.** A general command interface sits alongside specific operations for reading files, editing text, and controlling processes. The model can directly specify a path, a range of lines, or text to replace. The application can also grant access by operation. The cost is maintaining more tools and managing overlapping functionality so that choosing a tool remains straightforward.

Harness takes the second approach. Shell access lets the model run scripts, invoke command-line programs, and combine operations. Dedicated tools provide consistent interfaces for common tasks such as reading part of a file, making an exact text edit, waiting for a process, or interacting with a desktop.

There are two main reasons for this choice. First, explicit parameters and results let the model express an operation directly and let Harness check permission for that operation. Second, different tasks need different capabilities. An application can expose only file-reading tools for a research task and add shell access when a task needs to run an analysis script. Harness assembles the available tools from the environment's capabilities, operation permissions, and application configuration.

For CSV analysis, the model can inspect the data and write a script with file tools, run Python through the shell, then use process tools to follow execution and file tools to inspect the chart. The model uses the same tool interfaces whether the backend uses local APIs, Docker, or a provider such as E2B, Modal, or Daytona.

## How a call reaches the right environment

Once the model has chosen a tool and its arguments, Harness sends the call to the appropriate execution environment.

The first step is target selection. The application specifies which environments the task may access, and Harness supplies their names and path roots to the model. File operations select an environment by file path. Commands can select one through a working directory or an environment name. When neither is specified, Harness uses the application's configured default.

Harness then checks whether the selected environment permits the operation and prepares the environment and connection if needed. For example, when the model asks to run a script in a working directory, Harness checks that command execution is allowed in the environment selected by that directory. Connections can be established on first use, and environments that are already ready can be used through the same interfaces.

Finally, Harness dispatches the operation to the backend. A local backend can call operating-system APIs; a remote backend can use a vendor SDK or an execution service running in the target environment. Files are read, scripts run, and charts are saved in that selected environment. Status and output return to the model through the tool result.

The model chooses operations and arguments. Harness handles target selection, permission checks, and dispatch. The execution environment provides the actual compute and storage.

## Returning results and continuing the task

An analysis script might finish quickly or take several minutes. Execution environments can support both foreground execution and background startup to accommodate different workloads. For example, the [E2B JavaScript SDK](https://github.com/e2b-dev/E2B/blob/main/packages/js-sdk/src/sandbox/commands/index.ts) offers both:

- **Foreground execution** waits for the command to finish and returns its result. This is convenient for short operations.
- **Background startup** returns a command handle while the program continues running in the environment. The caller can receive logs through output callbacks and call `wait()` to wait for completion and retrieve the result. This suits longer tasks and programs intended to keep running.

Harness exposes these capabilities through tools. When the model calls `shell_exec` on an environment with process controls, Harness waits for an initial interval. If the command finishes during that interval, it returns the result. Otherwise, it returns an execution reference and the current status, so the model can follow up with tools such as `shell_wait`. The model uses the same command tool in either case; it does not have to choose a background mode in advance.

![A short command returns its final result; a longer command returns a reference while the same program continues running.](command-waiting.svg "Figure 2. In environments with process controls, shell_exec waits briefly before returning either a final result or an execution reference.")

For a backend that only supports foreground execution, the underlying call waits for the command to finish. Whether Harness keeps the model waiting for that result also depends on the application's tool-execution policy.

Short commands can therefore return a result in one call, while longer commands remain available to follow up on. When parallel execution is allowed, the agent can make progress on independent work while a command runs.

Tool results need both execution status and output. Status tells the model whether a program is still running, has finished, or has failed. Output supplies the analysis results or error details. Together, they inform the next action.

## From reading a CSV to delivering a chart

Now put the pieces together for the original task: analyze monthly sales and generate a chart. Assume the application has placed `sales.csv` in the selected environment, supplied `/workspace/sales.csv` as the input path, and granted file read/write access, command execution, and process controls.

1. **Provide context and tools.** Harness supplies the environment's name, path root, permitted operations, and availability, along with the relevant file and shell tools and their instructions. The model can identify which environment `/workspace` refers to and how to read files, run commands, and follow a process.

2. **Inspect the data and prepare a script.** The model uses a file tool to read the CSV header and sample rows, confirming the actual names and formats of the date and sales fields. It then writes `/workspace/analyze.py` to aggregate sales by month and plot the results. File contents come from tool results; environment context provides the access information.

3. **Execute in the selected environment.** The model calls `shell_exec` with `/workspace` as the working directory and `python analyze.py` as the command. Harness selects the target from the path, checks permission, prepares a connection if needed, and dispatches the call. The script uses the environment's CPU to process data and its filesystem to read the CSV and save the chart.

4. **Follow the execution state.** If the script finishes quickly, the model receives the result immediately. If it is still running, Harness returns an execution reference, and the model uses `shell_wait` to wait and retrieve output. If execution reports an invalid field format, the model can inspect the relevant data, revise the script, and run it again. Each decision follows from the returned status and information.

5. **Check and deliver the output.** After the script finishes successfully, the model uses a file tool to inspect the chart and check its months, values, and labels against the task. It then explains the sales trends and delivers the chart through the application's file display or download mechanism.

![A numbered CSV workflow connecting the LLM, context and tools inside Harness, and the sandbox where the script runs.](csv-workflow.svg "Figure 3. From context to a verified chart: model decisions become tool calls, and execution results inform the next decision.")

Throughout this process, the model chooses its next action from the task and execution results. Harness supplies consistent context and tools and routes calls to the selected environment. The environment performs the computation and file operations. The analysis is completed through this sequence of calls and feedback.

This post has focused on how the calling side brings those pieces together. In the next post, we will look inside the execution environment: how it receives requests, runs commands, handles output, and manages the resources those operations create.
