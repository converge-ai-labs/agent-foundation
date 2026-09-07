## Best practices

- Read the target snippet immediately before editing when context may be stale.
- `old_string` must match exactly, including whitespace and indentation.
- Preserve indentation from `view` output and ignore displayed line-number prefixes.
- Include enough surrounding context to make the match unique.
- Use `multi_edit` for multiple changes to the same file.
