---
title: How an execution environment runs a command
description: Follow a Python script from a Harness tool call to a running process. See how envd captures its output and what survives when the session closes.
date: '2026-11-24'
authors:
  - name: Yuxuan Chen
    url: https://github.com/YuxuanChen98
    avatar: https://avatars.githubusercontent.com/u/42828324?s=80
---

In the [previous post](/blog/how-agents-use-execution-environments/), the model inspected a sales CSV and wrote a script to total the sales for each month. Harness then ran `python analyze.py` in `/workspace`. The script takes a few minutes. In the meantime, the model can check the report requirements and come back for the chart.

Starting the script and checking the result are two separate tool calls. While the model reads the report template, what keeps track of Python on the target machine and collects its output? In this example, that's the job of envd.

## Why do we need envd?

The sales data and the analysis script both live in `/workspace` on the target machine. Harness needs a way to read files there, start Python there, and get the results back. a13n's execution service, envd, runs on the target machine or inside the sandbox. It gives Harness a common interface for working with files and processes across machines.

For the sales report, envd has four jobs.

- **Read and write files.** Harness can use envd to read the header of `sales.csv`, save the model's script as `analyze.py`, and retrieve the resulting `chart.png`.
- **Start and manage processes.** envd starts the command in the requested working directory. Harness can check on a background process, wait for it to finish, or ask envd to stop it if the task is cancelled.
- **Capture command output.** envd records standard output and standard error separately. The model can come back to read the script's progress messages or see why Python failed.
- **Manage sessions and their resources.** envd keeps track of the processes and output that belong to each execution session. When a session closes or expires, envd cleans up its running processes, command records, and temporary output.

![envd connects workspace files, a running Python command, separate output streams, and the session that owns temporary resources.](envd-capabilities.svg "Figure 1. What envd manages on the target machine during the sales analysis.")

An **execution session** groups operations for a single owner, typically across one or more agent runs in the same thread. Each session has a default working directory and keeps a record of the processes it starts and the output they produce.

A **run** is one execution of the agent and can involve several tool calls. A session can span runs. When a run finishes normally, Harness releases its use of the session, and envd can keep the session available. Closing the session explicitly, or letting it expire, triggers cleanup of the session as a whole.

## How does a tool call reach envd?

One way to run envd is as an HTTP server. When the model requests a command, Harness checks that the current environment allows it. The environment adapter then uses the envd client to send an HTTP request with the session identifier. envd uses that identifier to find the session, starts the command there, and sends the result back in the HTTP response.

![Harness checks shell access and sends process.start over HTTP. envd registers the command and both output pipes in the session before launch, then returns a handle.](command-request.svg "Figure 2. Starting Python in the background. The session owns the process and its output before the program runs.")

On the same machine, Harness and envd can also communicate over standard input and output. A remote machine can open a reverse WebSocket connection to receive requests. All three transports support the same operations for starting commands and checking on processes.

## How does a command become a process?

envd prepares the command using the working directory and shell configuration in the request. The working directory determines how relative paths resolve. Python looks for `analyze.py` in `/workspace`. When the script calls `open("sales.csv")`, it opens `/workspace/sales.csv`.

Before the shell runs the command, envd checks that the directory is accessible and that it has capacity for another process. It registers the execution with the session, reserves space for standard output and standard error, and connects a pipe for each stream. The shell can now start Python, which reads the CSV and adds up the sales.

That order matters even for a script that runs for a fraction of a second. If the plotting library is missing, Python might exit at the first `import`. By then envd is already tracking the process and reading its output, so Harness can get both the exit code and the error naming the missing library.

## Who looks after a background process?

envd can run a command in the foreground or start it in the background. A foreground call waits for the command to finish, then returns its status and output. A background call returns a process handle once envd confirms that the program has started. Both can run the same command in the same directory, and envd tracks the process in either case.

Suppose the tool call returns while Python is still adding up January's sales. The model can use a file tool to check which currency units the report template calls for. When it next calls the wait tool, Harness uses the handle to find that same execution. If the script is still running, Harness can wait for it. Once it finishes, Harness can retrieve its exit status and output.

## Where does the output go while the model is busy?

Suppose the script prints a progress message for each month it finishes. Even while Harness is busy elsewhere, envd keeps reading standard output and standard error, appending each to its own temporary file. An operating system pipe has limited capacity. If nobody reads from it and it fills up, Python can block on its next write. By draining the pipes as the script runs, envd lets it keep making progress.

![Two nine-byte progress messages in stdout and an empty stderr stream. Harness can read the second message from byte nine while its stderr offset is still zero.](command-output.svg "Figure 3. A second stdout read picks up at byte nine, just after the first message and its newline.")

Harness can read a chunk of saved output, keep track of its position, and pick up there on the next read. For short output, the preview returned when the command finishes is usually enough. For a longer error message, Harness can read further. Each stream has a size limit. If either exceeds its limit, envd terminates the command, keeps the bytes it has retained, and marks the output as incomplete.

## What stays after the script finishes?

When the script exits successfully, Harness can retrieve the exit code, read the log, and use a file tool to open `/workspace/chart.png`. The exit code tells Harness how the command ended. The log helps diagnose problems with the data. The chart is the file the user asked for.

Process records and logs remain available for inspection after the command finishes. The caller can release them when it is done. envd also periodically reclaims completed records that have gone unused. If capacity runs low, envd first clears eligible history. If that still doesn't free enough space, it declines to start the new command and lets healthy processes carry on.

A brief disconnect while Python is drawing the chart can leave the script running. As long as the original session remains valid, Harness can reconnect and check whether Python is still working or has already saved the file.

When the application closes the session or the session expires, envd begins stopping the processes it still manages and clearing their temporary output and records. A chart written to a persistent working directory survives session cleanup. How long it remains available depends on the lifetime of the storage backing that directory.

![The model reads the report template while Python aggregates sales. Harness later checks the chart. Closing the session reclaims the process record and logs, while the chart stays in persistent storage.](background-lifetime.svg "Figure 4. The tool call returns, Python exits, and the session closes at three different points in the same analysis.")
