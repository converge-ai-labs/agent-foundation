<best-practices>
- Prefer `multi_edit` over multiple edits to the same file.
- Do not issue concurrent edits to the same file; combine them in one call.
- Each `old_string` must be unique unless `replace_all=true`.
- Edits apply sequentially, so earlier edits must not invalidate later matches.
- Avoid overlapping replacements; one failed edit leaves the file unchanged.
</best-practices>
