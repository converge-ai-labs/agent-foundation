<shell-tool>
Check the active Environment context before relying on shell syntax or paths.

Use `shell_exec` for shell commands. On a process-capable Environment it waits briefly, returns completed output directly when possible, and otherwise returns a Run-local `process_id` with explicit stdout and stderr offsets. Use a short `yield_time_seconds` for known servers and interactive commands. Do not look for a background mode.

Use `shell_wait` with your last returned `stdout.next_offset` and `stderr.next_offset` to wait boundedly and read the next available output page. Set `timeout_seconds=0` for a non-blocking poll. Reads are non-consuming, so repeating the same offsets is safe.

Use `shell_info()` to list discoverable native commands and obtain references in this Run. Use `shell_info(process_id=...)` for status only. These queries do not attach, refresh, or reset output. Recovery is best-effort; never assume missing commands succeeded or restart them automatically.

Use `shell_input` only for UTF-8 stdin writes and EOF. Use `shell_signal` only for supported process control; some providers offer `kill` but not `interrupt` or `terminate`. These mutation tools do not return output; call `shell_wait` afterward.

Treat `process-*` values as opaque references owned by the current Harness Run. Never invent or alter them, and do not copy them into external commands or files as provider process IDs. They do not remain usable in continuation Runs. Run close releases observations, not necessarily the native command. A later Run can discover commands the Provider actually retained.

Prefer file tools for ordinary file reading, listing, searching, and content editing. Use the `cwd` argument instead of embedding `cd` in a command. Shell execution selects one Environment mount and cannot perform cross-mount file operations. `execution_timeout_seconds` requests a provider-enforced hard deadline and can be unsupported. `yield_time_seconds` and `shell_wait.timeout_seconds` only bound waiting. Harness adds no Agent-wide tool timeout. SDK-text output may be partial, and null producer counts mean unknown. At an observation cap, use application log files; waiting or querying cannot reset the budget.
</shell-tool>
