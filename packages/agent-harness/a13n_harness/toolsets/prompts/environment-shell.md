<shell-tool>
Check the active Environment context before relying on shell-specific syntax or paths.
Use `environment_shell_exec` for bounded foreground commands. Use `environment_process_start` for long-running commands, servers, builds, and test suites, then use the process tools for output, stdin, status, signals, waiting, and cleanup.
Prefer file tools for ordinary file reading, listing, searching, and content editing.
Use the `cwd` argument instead of embedding `cd` in a command.
Shell execution selects one Environment binding and cannot perform cross-binding file operations.
</shell-tool>
