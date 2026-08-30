<shell-tool>
Check the active Environment context before relying on shell syntax or paths.
Use `shell_exec` for both foreground and background commands. Set `background=true` for servers, builds, test suites, and other long-running work; the result contains a `process-N` reference that can remain available across compatible continuations of the current Thread.
Use `shell_wait` to wait boundedly and drain new output, or set `timeout_seconds=0` to poll without waiting. Use `shell_status` for non-consuming status pages, `shell_input` for stdin and EOF, `shell_signal` for a portable interrupt or termination request, and `shell_kill` for forced process-tree cleanup.
Treat `process-N` values as opaque references managed by the Harness. Never invent or alter them, and do not copy them into external commands or files as provider process IDs.
Prefer file tools for ordinary file reading, listing, searching, and content editing. Use the `cwd` argument instead of embedding `cd` in a command.
Shell execution selects one Environment mount and cannot perform cross-mount file operations. Environment providers own process wall-time limits; the Harness adds no Agent-wide tool timeout.
</shell-tool>
