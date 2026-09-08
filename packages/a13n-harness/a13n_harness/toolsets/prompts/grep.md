<best-practices>
- `pattern` is a regular expression by default. For exact code fragments, punctuation, or uncertain escaping, set `regex=false`; set `case_sensitive=false` to ignore case.
- Examples: `pattern="class Choice|class .*Picker|choices\\(|resume"` searches alternatives; `pattern="choices(", regex=false` searches literal code.
- Narrow `root` and `include` before raising limits. `include` uses the same glob syntax as `glob`: `*.py` at all depths, `/*.py` only at the root, or `{src,tests}/**/*.{py,rs}` for alternatives. Do not use regex alternation in `include`.
- Matching is line-based. Portable regex uses literals, classes, grouping, alternation, anchors and quantifiers. Direct Local and E2B use Python `re`; envd uses Rust `regex` and rejects lookaround and backreferences. Engine-specific syntax and Unicode edge cases are not portable.
- Zero matches are success. Invalid patterns return `error.details.field`, `reason`, and a correction `hint`; fix that field rather than retrying unchanged. Prefer literal mode when regex syntax is unnecessary.
- If `has_more` is true, continue at `next_offset` with unchanged filters on a stable filesystem. `max_matches_per_file` caps each file; `max_files` is a scan ceiling, not a request for silently incomplete results.
- Prefer `glob` first when candidate file names are not yet known.
- Keep context low for broad scans and raise it for focused inspection.
- Increase per-file or total result limits only after narrowing the search.
- Include hidden or ignored paths only when the target is likely there.
</best-practices>
