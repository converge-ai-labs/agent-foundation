<glob-tool>
<best-practices>
- Narrow the pattern and root before increasing result limits.
- Prefer `glob` before `grep` when candidate paths are not yet known.
- Bare patterns match recursively; use an anchored pattern when only root-level matches are intended.
- Include hidden or ignored paths only when the target is likely there.
- Use unlimited results only after narrowing the scope.
</best-practices>
</glob-tool>
