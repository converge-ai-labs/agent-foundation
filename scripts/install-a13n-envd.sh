#!/bin/sh
# Standalone native installer; independent of Harness UI's managed runtime.
set -eu

fail() { printf 'a13n-envd installer: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh install-a13n-envd.sh [--version X.Y.Z[-rc.N]] [--install-dir /absolute/path]
                               [--add-to-path | --no-add-to-path]

Install the native a13n-envd binary for Linux or macOS; do not start it.
Defaults: newest stable envd release; ~/.local/bin (/usr/local/bin for root);
no PATH changes. Explicit flags override these environment variables:
  A13N_ENVD_VERSION       Exact canonical release version
  A13N_ENVD_INSTALL_DIR   Absolute destination directory
  A13N_ENVD_ADD_TO_PATH   1 to opt in, 0 to leave PATH unchanged
Requires curl, tar, sha256sum or shasum; jq for automatic version selection.
EOF
}
version=${A13N_ENVD_VERSION:-}
install_dir=${A13N_ENVD_INSTALL_DIR:-}
add_to_path=${A13N_ENVD_ADD_TO_PATH:-0}
path_flag=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --version|--install-dir)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "$1 requires a value"
            case "$1" in --version) version=$2 ;; --install-dir) install_dir=$2 ;; esac
            shift 2 ;;
        --add-to-path|--no-add-to-path)
            [ -z "$path_flag" ] || [ "$path_flag" = "$1" ] || fail "PATH flags are mutually exclusive"
            path_flag=$1
            case "$1" in --add-to-path) add_to_path=1 ;; *) add_to_path=0 ;; esac
            shift ;;
        *) fail "unknown argument: $1" ;;
    esac
done
case "$add_to_path" in 0|1) ;; *) fail 'A13N_ENVD_ADD_TO_PATH must be 0 or 1' ;; esac
if [ -n "$version" ]; then
    [ "$(printf '%s' "$version" | tr -d '\r\n')" = "$version" ] || fail 'invalid release version'
    printf '%s\n' "$version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.[1-9][0-9]*)?$' || fail "invalid release version: $version"
fi
if [ -z "$install_dir" ]; then
    if [ "$(id -u)" = 0 ]; then install_dir=/usr/local/bin; else install_dir=${HOME:?}/.local/bin; fi
fi
case "$install_dir" in /*) ;; *) fail 'install directory must be absolute' ;; esac
case "$(uname -s):$(uname -m)" in
    Linux:x86_64) target=x86_64-unknown-linux-gnu ;;
    Linux:aarch64|Linux:arm64) target=aarch64-unknown-linux-gnu ;;
    Darwin:x86_64) target=x86_64-apple-darwin ;;
    Darwin:arm64|Darwin:aarch64) target=aarch64-apple-darwin ;;
    *) fail 'unsupported platform (expected Linux/macOS x86_64 or ARM64)' ;;
esac
for tool in curl tar; do command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"; done
if command -v sha256sum >/dev/null 2>&1; then checksum=sha256sum
elif command -v shasum >/dev/null 2>&1; then checksum=shasum
else fail 'sha256sum or shasum is required'; fi

# Only opt-in PATH changes touch shell startup files. Quote paths as data.
profile=
path_line=
if [ "$add_to_path" = 1 ]; then
    case "$install_dir" in *:*) fail 'a PATH entry cannot contain a colon' ;; esac
    [ "$(printf '%s' "$install_dir" | tr -d '\r\n')" = "$install_dir" ] || fail 'a PATH entry cannot contain newlines'
    shell_name=${SHELL:-sh}
    shell_name=${shell_name##*/}
    case "$shell_name" in
        bash)
            if [ "$(uname -s)" = Darwin ]; then profile=${HOME:?}/.bash_profile
            else profile=${HOME:?}/.bashrc; fi ;;
        zsh) profile=${ZDOTDIR:-${HOME:?}}/.zshrc ;;
        sh|dash|ksh) profile=${HOME:?}/.profile ;;
        fish) profile=${XDG_CONFIG_HOME:-${HOME:?}/.config}/fish/config.fish ;;
        *) fail 'unsupported shell for --add-to-path; set PATH manually' ;;
    esac
    if [ "$shell_name" = fish ]; then
        quoted=$(printf '%s' "$install_dir" | sed "s/\\\\/\\\\\\\\/g; s/'/\\\\'/g")
        path_line="fish_add_path -- '$quoted'"
    else
        quoted=$(printf '%s' "$install_dir" | sed "s/'/'\\\\''/g")
        path_line="export PATH='$quoted':\"\$PATH\""
    fi
fi

download() {
    curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --connect-timeout 10 --max-time 120 --retry 3 "$1" -o "$2"
}
mkdir -p "$install_dir"
[ ! -d "$install_dir/a13n-envd" ] && [ ! -L "$install_dir/a13n-envd" ] || fail 'destination must not be a directory or symlink'
umask 077
stage=$(mktemp -d "$install_dir/.a13n-envd.XXXXXX")
trap 'rm -rf "$stage"' EXIT
trap 'exit 1' HUP INT TERM

if [ -z "$version" ]; then
    command -v jq >/dev/null 2>&1 || fail 'jq is required to select the newest stable release; alternatively supply --version'
    page=1
    while :; do
        download "https://api.github.com/repos/converge-ai-labs/agent-foundation/releases?per_page=100&page=$page" "$stage/releases.json"
        jq -e 'type == "array"' "$stage/releases.json" >/dev/null || fail 'invalid GitHub release list'
        version=$(jq -r '[.[] | select(.draft == false and .prerelease == false) | .tag_name |
            select(test("^release/a13n-envd-v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))][0] // empty |
            sub("^release/a13n-envd-v"; "")' "$stage/releases.json")
        [ -z "$version" ] || break
        [ "$(jq 'length' "$stage/releases.json")" -gt 0 ] || fail 'no stable envd release found'
        page=$((page + 1))
    done
fi
archive="a13n-envd-$version-$target.tar.gz"
release="https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v$version"
download "$release/$archive" "$stage/$archive"
download "$release/SHA256SUMS" "$stage/SHA256SUMS"
expected=$(awk -v name="$archive" '$2 == name || $2 == "*" name { if (NF != 2) exit 1; print $1; count++ } END { if (count != 1) exit 1 }' "$stage/SHA256SUMS") || fail 'missing or ambiguous archive checksum'
printf '%s\n' "$expected" | grep -Eq '^[0-9a-fA-F]{64}$' || fail 'invalid archive checksum'
if [ "$checksum" = sha256sum ]; then actual=$(sha256sum "$stage/$archive")
else actual=$(shasum -a 256 "$stage/$archive"); fi
actual=${actual%% *}
[ "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" = "$actual" ] || fail 'archive SHA256 mismatch'

# Validate the two regular entries, then stream only the binary to a new file.
# No archive-supplied path is ever extracted onto the filesystem.
tar -tzf "$stage/$archive" > "$stage/entries"
[ "$(wc -l < "$stage/entries" | tr -d ' ')" = 2 ] &&
    [ "$(grep -cx 'a13n-envd' "$stage/entries")" = 1 ] &&
    [ "$(grep -cx 'LICENSE' "$stage/entries")" = 1 ] || fail 'malformed archive entries'
tar -tvzf "$stage/$archive" > "$stage/types"
awk 'substr($0, 1, 1) != "-" { exit 1 }' "$stage/types" || fail 'archive entries must be regular files'
tar -xOzf "$stage/$archive" a13n-envd > "$stage/a13n-envd"
[ -s "$stage/a13n-envd" ] || fail 'archive contains an empty executable'
chmod 755 "$stage/a13n-envd"
mv -f "$stage/a13n-envd" "$install_dir/a13n-envd"
printf 'Installed a13n-envd %s to %s/a13n-envd\n' "$version" "$install_dir"

if [ "$add_to_path" = 1 ]; then
    mkdir -p "$(dirname "$profile")"
    if [ ! -f "$profile" ] || ! grep -Fqx "$path_line" "$profile"; then
        printf '\n# a13n-envd standalone installer\n%s\n' "$path_line" >> "$profile"
    fi
    printf 'PATH configured in %s; open a new shell to use it.\n' "$profile"
fi
