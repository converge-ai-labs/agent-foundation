<best-practices>
- Narrow the pattern and root before increasing result limits.
- Prefer `glob` before `grep` when candidate paths are not yet known.
- Patterns are relative to `root`. Bare `*.py` searches all depths; `/*.py` selects only root-level names; `src/*.py` does not descend below `src`; `src/**/*.py` does.
- Use brace alternatives for multiple paths or extensions: `{src,tests}/**/*.{py,rs}`. Groups must be non-nested with nonempty alternatives (at most 256 expanded patterns). No backslash escapes or numeric ranges; `**` must occupy a whole path segment.
- Empty results are successful. If `has_more` is true, use `next_offset` with unchanged arguments for the next page; offsets assume a stable filesystem.
- Include hidden or ignored paths only when the target is likely there.
- Use unlimited results only after narrowing the scope.
</best-practices>
