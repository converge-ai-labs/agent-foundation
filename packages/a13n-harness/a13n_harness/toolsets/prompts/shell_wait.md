<best-practices>
Use `shell_wait` with your last returned `stdout.next_offset` and `stderr.next_offset` to wait boundedly and read the next available output page. Set `timeout_seconds=0` for a non-blocking poll. Reads are non-consuming, so repeating the same offsets is safe.
</best-practices>
