# Managed Skill Package Contract

## Design Position

A managed Skill package is a bounded directory whose root contains exactly one
Harness-compatible `SKILL.md`. This contract is independent of any one Host or
acquisition source. A Host can support any subset of the shared ZIP and GitHub
sources and can define additional explicitly authorized sources, provided every
accepted input normalizes to the same manifest and content digest. Accepted content
becomes immutable Host-owned content before an Agent can select it. Upload receipts,
repository refs, and local paths are never runtime authority.

This document owns only the portable package, shared ZIP and GitHub source
normalization, content digest, and safety limits. The
[Harness](agent-harness/09-context-and-memory.md#skills-and-discovery)
owns `SKILL.md` parsing, discovery, selection, instructions, and `SkillPath` values.
Each Host owns resource identity, APIs, authorization, storage, retention, and
runtime materialization, including which source forms it accepts.

## Package Model

These types are conceptual. Hosts can use different storage envelopes as long as
they preserve the same manifest and file bytes.

```python
class ManagedSkillPackageFile:
    path: str
    size_bytes: int
    sha256: str


class ManagedSkillPackageManifest:
    schema_version: Literal["1"]
    skill_name: str
    description: str
    harness_skill_contract: Literal["1"]
    files: tuple[ManagedSkillPackageFile, ...]
    total_size_bytes: int
    content_digest: str


class ZipSkillImportProvenance:
    kind: Literal["zip"]
    archive_sha256: str


class GitHubSkillImportProvenance:
    kind: Literal["github"]
    repository_url: str
    requested_ref: str | None
    resolved_commit_sha: str
    subdirectory: str

```

`files` is ordered by normalized path and identifies the corresponding immutable
file payloads. `skill_name` and `description` come from the normalized `SKILL.md`
under the selected Harness contract; request metadata cannot override them.
Provenance records how content was acquired but never authorizes a later read.
Credentials and native Host paths are not provenance. A Host that accepts another
source form owns its additional safe provenance shape; provenance never changes the
portable package manifest or content digest.

Every SHA-256 value is 64 lowercase hexadecimal characters. `content_digest` is
SHA-256 over `UTF8("a13n.managed-skill-package.v1\n")` followed by the RFC 8785
canonical JSON encoding of the manifest with `content_digest` omitted. The ordered
file entries contain the payload digests, so the result commits all package bytes.
ZIP encoding, Git commit identity, timestamps, permissions, and provenance do not
affect content identity.

## Package Rules and Limits

Paths are relative Unicode-NFC POSIX paths. They contain no empty, `.`, or `..`
segments, control characters, backslashes, drive or URI prefixes, absolute forms,
or case-folded collisions. Segments cannot end in a space or dot or equal a Windows
device name.

Only regular files are accepted. Symlinks, hard links, submodules, devices, sockets,
FIFOs, and other special entries are rejected. Source permissions, ownership, and
timestamps are discarded; materialized files are ordinary non-executable files.
Nested archives remain ordinary files and are not recursively extracted.

The package contains one root `SKILL.md` with that exact case. No other path can end
in case-folded `SKILL.md`, preventing one package from becoming several discovered
Skills after materialization. The root file is valid UTF-8 and satisfies the
selected Harness contract; other files can be binary.

Version `1` has these hard maxima. A Host can configure lower admission limits but
cannot raise them without selecting a later package-contract version. MiB and KiB
are binary units.

| Value                              |                   Maximum |
| ---------------------------------- | ------------------------: |
| Uploaded ZIP body                  |                    64 MiB |
| ZIP members, including directories |                     8,192 |
| Regular package files              |                     4,096 |
| Total normalized file bytes        |                    64 MiB |
| One regular file                   |                    16 MiB |
| `SKILL.md`                         |                   256 KiB |
| Relative path depth                |               32 segments |
| Complete path / one segment        |   1,024 / 255 UTF-8 bytes |
| Skill name                         | 256 Unicode scalar values |
| Skill description                  |              16 KiB UTF-8 |
| GitHub tree entries inspected      |                     8,192 |

Declared metadata and actual streamed bytes are both checked. Crossing a limit
fails the complete import; a Host never truncates or silently drops files.

## ZIP and GitHub Sources

### ZIP

A ZIP request body contains one ordinary ZIP using stored or deflated entries. The
archive has either `SKILL.md` at its root or one wrapper directory containing the
whole package; normalization removes that wrapper. Every member is subject to the
path and file-kind rules. Duplicate normalized paths, root siblings outside the
wrapper, encryption, multi-disk archives, unsupported compression, malformed
metadata, or limit violations reject the import.

The Host validates compressed and expanded limits before producing a manifest. An
archive member never chooses a Host destination path.

### GitHub

```python
class GitHubSkillSource:
    repository_url: str
    ref: str | None = None
    subdirectory: str = ""
    expected_commit_sha: str | None = None
```

`repository_url` is an HTTPS `github.com/{owner}/{repository}` repository URL.
`ref` is a branch, tag, or full commit SHA; absence selects the default branch for
that import. `subdirectory` is a portable relative path whose root contains the
package `SKILL.md`.

The Host resolves the selector to one commit before reading files and verifies
`expected_commit_sha` when supplied. It fetches only the selected bounded tree and
does not execute repository content, initialize submodules, fetch Git LFS objects,
or install dependencies. Every accepted file comes from the resolved commit; an
incomplete read fails the import. GitHub access is allowlisted and redirects cannot
turn it into a general URL fetcher.

Each Host defines its own optional credential selector and resolves it only for the
current acquisition. Credential values, headers, signed URLs, and selectors are not
stored in the package or provenance. A mutable ref is resolved only during explicit
import; runtime never follows it.

## Normalization Result

Normalization returns the canonical manifest, its ordered file payloads, and safe
source provenance. It either succeeds for the complete package or returns no
package. A Host copies the result before a resource revision or Agent can select it
and never treats the mutable source as runtime content.

The result is content only. It grants no credential, Tool, plugin, Capability,
Environment, or execution authority.

## Failure and Compatibility

Normalization is all-or-nothing. Malformed or oversized content, unsafe file
kinds or paths, GitHub acquisition failure, and Harness validation failure return
no package and leave existing Host revisions unchanged.

Schema version, path normalization, limits, digest calculation, ZIP root handling,
GitHub selector meaning, and Harness contract selection are compatibility facts.
Host storage and source-acquisition implementations are not compatibility facts.

## Invariants

1. One package yields exactly one root Skill after normalization.
2. Inputs from any accepted source with equal normalized bytes have the same content digest.
3. Links, special files, path collisions, and partial packages are never accepted.
4. Source identity, credentials, and package content grant no runtime authority; a Host selects only copied immutable content.
