<shell-tool>
Check the active Environment context before relying on shell syntax or paths.

Use `shell_exec` for shell commands. On a process-capable Environment it waits briefly, returns completed output directly when possible, and otherwise returns a Run-owned `process_id` with explicit stdout and stderr offsets. Use a short `yield_time_seconds` for known servers and interactive commands. Do not look for a background mode.

Use `shell_wait` with your last returned `stdout.next_offset` and `stderr.next_offset` to wait boundedly and read the next retained output page. Set `timeout_seconds=0` for a non-blocking poll. Reads are non-consuming, so repeating the same offsets is safe.

Use `shell_input` only for UTF-8 stdin writes and EOF. Use `shell_signal` with `interrupt`, `terminate`, or `kill` only for process control. These mutation tools do not return output; call `shell_wait` afterward.

Treat `process-*` values as opaque references owned by the current Harness Run. Never invent or alter them, and do not copy them into external commands or files as provider process IDs. They do not remain usable in continuation Runs.

Prefer file tools for ordinary file reading, listing, searching, and content editing. Use the `cwd` argument instead of embedding `cd` in a command. Shell execution selects one Environment mount and cannot perform cross-mount file operations. Environment providers own total process wall-time limits; `yield_time_seconds` bounds only the initial wait and the Harness adds no Agent-wide tool timeout.
</shell-tool>
