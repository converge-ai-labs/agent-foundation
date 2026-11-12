<essential-rules>
- Read large text files in focused chunks.
- Read an existing target before overwriting it.
- Read the target snippet immediately before editing when context may be stale.
- Prefer `multi_edit` over multiple edits to the same file.
- Use `ls` for shallow directory inspection, `glob` for recursive discovery, and `grep` for content search.
- Narrow the pattern and root before increasing result limits.
- `pattern` is a regular expression by default. For exact code fragments, punctuation, or uncertain escaping, set `regex=false`; set `case_sensitive=false` to ignore case.
</essential-rules>

<move>
- Use explicit source and destination pairs and review broad batches before moving them.
- Enable overwrite only when replacement is intentional.
- Use `edit` or `multi_edit` for content changes rather than moving files through temporary paths.
</move>

<copy>
- Use explicit source and destination pairs and review broad batches before copying them.
- Use `copy` when data must cross Environment mounts; shell commands operate within one selected mount.
- Enable overwrite only when replacement is intentional.
</copy>

<delete>
- Prefer specific file or directory paths over broad parent directories.
- Verify broad recursive targets with `ls` or `glob` before deleting.
- Use recursive and force together only when broad cleanup is intentional.
</delete>

<!-- reference -->

<view>
- Use focused media instructions for OCR, transcription, timestamped review, UI QA, speaker labels, or specific detail extraction.
- If media analysis is vague or low-confidence, follow up with narrower instructions on the same file.
- Ask the analyzer to name uncertain or omitted details when completeness matters.
- Use PDF conversion tools for PDFs when page structure matters.
</view>

<write>
- For new files, verify the parent directory exists with `ls`.
- Use `edit` or `multi_edit` for partial changes.
- Avoid whole-file rewrites when preserving unrelated changes matters.
</write>

<edit>
- `old_string` must match exactly, including whitespace and indentation.
- Preserve indentation from `view` output and ignore displayed line-number prefixes.
- Include enough surrounding context to make the match unique.
- Use `multi_edit` for multiple changes to the same file.
</edit>

<multi-edit>
- Do not issue concurrent edits to the same file; combine them in one call.
- Each `old_string` must be unique unless `replace_all=true`.
- Edits apply sequentially, so earlier edits must not invalidate later matches.
- Avoid overlapping replacements; one failed edit leaves the file unchanged.
</multi-edit>

<ls>
- Filter noisy directories before listing broad paths.
- Use unlimited results only after narrowing the scope.
</ls>

<glob>
- Prefer `glob` before `grep` when candidate paths are not yet known.
- Patterns are relative to `root`. Bare `*.py` searches all depths; `/*.py` selects only root-level names; `src/*.py` does not descend below `src`; `src/**/*.py` does.
- Use brace alternatives for multiple paths or extensions: `{src,tests}/**/*.{py,rs}`. Groups must be non-nested with nonempty alternatives (at most 256 expanded patterns). No backslash escapes or numeric ranges; `**` must occupy a whole path segment.
- Empty results are successful. If `has_more` is true, use `next_offset` with unchanged arguments for the next page; offsets assume a stable filesystem.
- Include hidden or ignored paths only when the target is likely there.
- Use unlimited results only after narrowing the scope.
</glob>

<grep>
- Examples: `pattern="class Choice|class .*Picker|choices\\(|resume"` searches alternatives; `pattern="choices(", regex=false` searches literal code.
- `root` accepts a file or directory. An explicit file searches only itself, including hidden or Git-ignored files; `include` matches its basename. A directory is searched recursively with the selected hidden/ignore filters.
- Narrow `root` and `include` before raising limits. `include` uses the same glob syntax as `glob`: `*.py` at all depths, `/*.py` only at the root, or `{src,tests}/**/*.{py,rs}` for alternatives. Do not use regex alternation in `include`.
- Matching is line-based. Portable regex uses literals, classes, grouping, alternation, anchors and quantifiers. Direct Local and E2B use Python `re`; envd uses Rust `regex` and rejects lookaround and backreferences. Engine-specific syntax and Unicode edge cases are not portable.
- Zero matches are success. Invalid patterns return `error.details.field`, `reason`, and a correction `hint`; fix that field rather than retrying unchanged. Prefer literal mode when regex syntax is unnecessary.
- If `has_more` is true, continue at `next_offset` with unchanged filters on a stable filesystem. `max_matches_per_file` caps each file; `max_files` is a scan ceiling, not a request for silently incomplete results.
- Prefer `glob` first when candidate file names are not yet known.
- Keep context low for broad scans and raise it for focused inspection.
- Increase per-file or total result limits only after narrowing the search.
- Include hidden or ignored paths only when the target is likely there.
</grep>
