#!/bin/sh
# Seed the startup working directory, not subsequent Envd Session directories.
set -eu

# The default entrypoint retains root for Envd, but workspace writes must not.
# Native Docker callers already run as their selected container user.
if [ "$(id -u)" = 0 ] && [ "${A13N_ENVD_EXECUTION_UID:-1000}" != 0 ]; then
    exec setpriv --reuid "${A13N_ENVD_EXECUTION_UID:-1000}" \
        --regid "${A13N_ENVD_EXECUTION_GID:-1000}" --init-groups -- "$0" "$@"
fi

source_root=${A13N_SANDBOX_SKILLS_DIR:-/usr/local/share/a13n/skills}
target_root=./.agents/skills
staging=
trap 'if [ -n "$staging" ]; then rm -rf -- "$staging"; fi' EXIT
trap 'exit 1' HUP INT TERM

for source in "$source_root"/*; do
    [ -d "$source" ] && [ -f "$source/SKILL.md" ] || continue
    target="$target_root/$(basename "$source")"
    # A file or dangling symlink also belongs to the user; never replace it.
    if [ -e "$target" ] || [ -L "$target" ]; then
        continue
    fi
    if ! mkdir -p -- "$target_root"; then
        printf '%s\n' "a13n-sandbox-init: cannot create $target_root; skipping skills" >&2
        exit 0
    fi
    if ! staging=$(mktemp -d "$target_root/.a13n-skill.XXXXXX"); then
        printf '%s\n' "a13n-sandbox-init: cannot stage $target; skipping skill" >&2
        continue
    fi
    # Publish a complete directory so failed copies are not discovered as skills.
    if ! cp -R -- "$source/." "$staging/" || ! mv -T -n -- "$staging" "$target"; then
        printf '%s\n' "a13n-sandbox-init: cannot initialize $target; skipping skill" >&2
    fi
    rm -rf -- "$staging"
    staging=
done
