from __future__ import annotations

import io
import stat
import struct
import unicodedata
import zipfile
from collections.abc import Callable, Iterable

import pytest
from a13n_service.skills.package import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_NORMALIZED_ARCHIVE_BYTES,
    MAX_PATH_BYTES,
    MAX_PATH_DEPTH,
    MAX_SEGMENT_BYTES,
    MAX_SKILL_DESCRIPTION_BYTES,
    MAX_SKILL_DOCUMENT_BYTES,
    MAX_TOTAL_BYTES,
    SkillPackageError,
    normalize_skill_files,
    normalize_skill_zip,
    normalize_stored_skill_zip,
    skill_package_object_key,
)

SKILL_DOCUMENT = b"---\nname: deploy-helper\ndescription: Deploy a reviewed service.\n---\n\n# Workflow\n"


def archive(
    entries: Iterable[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for path, content in entries:
            info = zipfile.ZipInfo(path)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            target.writestr(info, content)
    return output.getvalue()


def error_code(callable_: Callable[[], object]) -> str:
    with pytest.raises(SkillPackageError) as captured:
        callable_()
    return captured.value.code


def patch_zip_field(candidate: bytes, signature: bytes, offset: int, value: int) -> bytes:
    patched = bytearray(candidate)
    position = patched.index(signature)
    struct.pack_into("<H", patched, position + offset, value)
    return bytes(patched)


def test_contract_limits_match_the_shared_version_one_package_contract() -> None:
    assert MAX_ARCHIVE_BYTES == 64 * 1024 * 1024
    assert MAX_ARCHIVE_MEMBERS == 8192
    assert MAX_FILES == 4096
    assert MAX_TOTAL_BYTES == 64 * 1024 * 1024
    assert MAX_FILE_BYTES == 16 * 1024 * 1024
    assert MAX_SKILL_DOCUMENT_BYTES == 256 * 1024
    assert MAX_SKILL_DESCRIPTION_BYTES == 16 * 1024
    assert MAX_PATH_DEPTH == 32
    assert MAX_PATH_BYTES == 1024
    assert MAX_SEGMENT_BYTES == 255
    assert MAX_NORMALIZED_ARCHIVE_BYTES == MAX_TOTAL_BYTES + MAX_FILES * (2 * MAX_PATH_BYTES + 76) + 22


def test_zip_normalization_removes_one_wrapper_and_produces_canonical_content() -> None:
    direct = normalize_skill_zip(
        archive(
            (
                ("references/runbook.md", b"runbook"),
                ("SKILL.md", SKILL_DOCUMENT),
            )
        )
    )
    wrapped = normalize_skill_zip(
        archive(
            (
                ("release-skill/SKILL.md", SKILL_DOCUMENT),
                ("release-skill/references/runbook.md", b"runbook"),
            ),
            compression=zipfile.ZIP_STORED,
        )
    )

    assert direct.manifest == wrapped.manifest
    assert direct.manifest.skill_name == "deploy-helper"
    assert direct.manifest.description == "Deploy a reviewed service."
    assert direct.manifest.total_size_bytes == len(SKILL_DOCUMENT) + len(b"runbook")
    assert [item.path for item in direct.files] == ["SKILL.md", "references/runbook.md"]
    assert direct.archive_bytes == wrapped.archive_bytes
    round_trip = normalize_skill_zip(direct.archive_bytes)
    assert round_trip.manifest == direct.manifest
    assert round_trip.files == direct.files


def test_content_identity_commits_paths_and_file_bytes_but_not_zip_metadata() -> None:
    first = normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "a.txt": b"one"}.items())
    reordered = normalize_skill_files({"a.txt": b"one", "SKILL.md": SKILL_DOCUMENT}.items())
    changed = normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "a.txt": b"two"}.items())

    assert first.manifest.content_digest == reordered.manifest.content_digest
    assert first.archive_bytes == reordered.archive_bytes
    assert changed.manifest.content_digest != first.manifest.content_digest
    assert first.manifest.content_digest == "479fc461d4d7e934a30c3f8f12997bf689b8d859e956ffad636c488a08e322fb"
    assert first.manifest.files[0].path == "SKILL.md"
    assert first.manifest.files[1].sha256 != changed.manifest.files[1].sha256


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/SKILL.md",
        "../SKILL.md",
        "folder/../SKILL.md",
        "folder//SKILL.md",
        "folder\\SKILL.md",
        "C:SKILL.md",
        "https:SKILL.md",
        "folder./file.txt",
        "folder /file.txt",
        "con/readme.md",
        "NUL.txt",
        "bad\x00name",
    ],
)
def test_file_tree_rejects_unsafe_paths(path: str) -> None:
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, path: b"unsafe"}.items())) == (
        "skill_package_invalid"
    )


def test_file_tree_normalizes_unicode_and_rejects_normalized_or_casefolded_collisions() -> None:
    decomposed = unicodedata.normalize("NFD", "café.txt")
    normalized = normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, decomposed: b"ok"}.items())
    assert normalized.files[1].path == "café.txt"

    for colliding in (
        (("Readme.md", b"one"), ("README.md", b"two")),
        ((decomposed, b"one"), ("café.txt", b"two")),
    ):
        files = (("SKILL.md", SKILL_DOCUMENT), *colliding)
        assert error_code(lambda files=files: normalize_skill_files(files)) == "skill_package_invalid"

    for ancestor_collision in (
        (("assets", b"file"), ("assets/icon.png", b"nested")),
        (("Assets", b"file"), ("assets/icon.png", b"nested")),
        (("Assets/one.png", b"one"), ("assets/two.png", b"two")),
    ):
        files = (("SKILL.md", SKILL_DOCUMENT), *ancestor_collision)
        assert error_code(lambda files=files: normalize_skill_files(files)) == "skill_package_invalid"


@pytest.mark.parametrize(
    "entries",
    [
        (("README.md", b"missing"),),
        (("skill.md", SKILL_DOCUMENT),),
        (("SKILL.md", SKILL_DOCUMENT), ("nested/SKILL.md", SKILL_DOCUMENT)),
        (("SKILL.md", b"not frontmatter"),),
        (("SKILL.md", b"\xff"),),
        (("SKILL.md", b"---\nname: x\n---\n"),),
    ],
)
def test_package_requires_exactly_one_contract_valid_root_skill_document(
    entries: tuple[tuple[str, bytes], ...],
) -> None:
    assert error_code(lambda: normalize_skill_files(entries)) == "skill_package_invalid"


def test_foundation_parses_skill_frontmatter_without_a_harness_api() -> None:
    package = normalize_skill_files(
        (
            (
                "SKILL.md",
                b"\xef\xbb\xbf---\nname: ' deploy-helper '\ndescription: ' Reviewed deploys. '\nignored: true\n---\nbody",
            ),
        )
    )

    assert package.manifest.skill_name == "deploy-helper"
    assert package.manifest.description == "Reviewed deploys."


@pytest.mark.parametrize(
    "frontmatter",
    [
        "[]",
        "name: [",
        "name: deploy-helper\ndescription: true",
        "name: true\ndescription: deploy safely",
        'name: "\\0"\ndescription: deploy safely',
        'name: deploy-helper\ndescription: "\\0"',
    ],
)
def test_foundation_rejects_invalid_skill_frontmatter_metadata(frontmatter: str) -> None:
    document = f"---\n{frontmatter}\n---\n".encode()

    assert error_code(lambda: normalize_skill_files((("SKILL.md", document),))) == "skill_package_invalid"


def test_skill_description_limit_counts_utf8_bytes() -> None:
    boundary = "é" * (MAX_SKILL_DESCRIPTION_BYTES // len("é".encode()))
    accepted = normalize_skill_files(
        (("SKILL.md", f"---\nname: deploy-helper\ndescription: {boundary}\n---\n".encode()),)
    )
    assert accepted.manifest.description == boundary

    oversized = boundary + "é"
    assert (
        error_code(
            lambda: normalize_skill_files(
                (("SKILL.md", f"---\nname: deploy-helper\ndescription: {oversized}\n---\n".encode()),)
            )
        )
        == "skill_package_limit"
    )


def test_zip_rejects_wrapper_siblings_duplicate_members_links_and_unsupported_compression() -> None:
    siblings = archive((("wrapper/SKILL.md", SKILL_DOCUMENT), ("outside.txt", b"unsafe")))
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicates = archive((("SKILL.md", SKILL_DOCUMENT), ("SKILL.md", SKILL_DOCUMENT)))

    link_output = io.BytesIO()
    with zipfile.ZipFile(link_output, "w") as target:
        link = zipfile.ZipInfo("SKILL.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(link, b"target")

    unsupported = archive((("SKILL.md", SKILL_DOCUMENT),), compression=zipfile.ZIP_BZIP2)

    ordinary = archive((("SKILL.md", SKILL_DOCUMENT),))
    encrypted = patch_zip_field(ordinary, b"PK\x03\x04", 6, 1)
    encrypted = patch_zip_field(encrypted, b"PK\x01\x02", 8, 1)
    multi_disk = patch_zip_field(ordinary, b"PK\x01\x02", 34, 1)

    special_directory_output = io.BytesIO()
    with zipfile.ZipFile(special_directory_output, "w") as target:
        skill = zipfile.ZipInfo("SKILL.md")
        skill.create_system = 3
        skill.external_attr = (stat.S_IFREG | 0o644) << 16
        target.writestr(skill, SKILL_DOCUMENT)
        disguised_link = zipfile.ZipInfo("nested/")
        disguised_link.create_system = 3
        disguised_link.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(disguised_link, b"")

    data_directory_output = io.BytesIO()
    with zipfile.ZipFile(data_directory_output, "w") as target:
        target.writestr("SKILL.md", SKILL_DOCUMENT)
        data_directory = zipfile.ZipInfo("nested/")
        data_directory.create_system = 3
        data_directory.external_attr = (stat.S_IFDIR | 0o755) << 16
        target.writestr(data_directory, b"hidden")

    for candidate in (
        siblings,
        duplicates,
        link_output.getvalue(),
        unsupported,
        encrypted,
        multi_disk,
        special_directory_output.getvalue(),
        data_directory_output.getvalue(),
    ):
        assert error_code(lambda candidate=candidate: normalize_skill_zip(candidate)) == "skill_package_invalid"


def test_size_count_and_path_limits_fail_with_the_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_service.skills.package as package_module

    monkeypatch.setattr(package_module, "MAX_FILES", 1)
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "extra": b"x"}.items())) == (
        "skill_package_limit"
    )

    monkeypatch.setattr(package_module, "MAX_FILES", MAX_FILES)
    monkeypatch.setattr(package_module, "MAX_FILE_BYTES", len(SKILL_DOCUMENT) - 1)
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT}.items())) == "skill_package_limit"

    monkeypatch.setattr(package_module, "MAX_FILE_BYTES", MAX_FILE_BYTES)
    monkeypatch.setattr(package_module, "MAX_TOTAL_BYTES", len(SKILL_DOCUMENT))
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "extra": b"x"}.items())) == (
        "skill_package_limit"
    )

    monkeypatch.setattr(package_module, "MAX_TOTAL_BYTES", MAX_TOTAL_BYTES)
    monkeypatch.setattr(package_module, "MAX_SKILL_DOCUMENT_BYTES", len(SKILL_DOCUMENT) - 1)
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT}.items())) == "skill_package_limit"

    monkeypatch.setattr(package_module, "MAX_SKILL_DOCUMENT_BYTES", MAX_SKILL_DOCUMENT_BYTES)
    monkeypatch.setattr(package_module, "MAX_PATH_DEPTH", 1)
    assert error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "a/b": b"x"}.items())) == (
        "skill_package_limit"
    )

    monkeypatch.setattr(package_module, "MAX_PATH_DEPTH", MAX_PATH_DEPTH)
    monkeypatch.setattr(package_module, "MAX_SEGMENT_BYTES", 4)
    assert (
        error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "abcde": b"x"}.items()))
        == "skill_package_limit"
    )

    monkeypatch.setattr(package_module, "MAX_SEGMENT_BYTES", MAX_SEGMENT_BYTES)
    monkeypatch.setattr(package_module, "MAX_PATH_BYTES", 4)
    assert (
        error_code(lambda: normalize_skill_files({"SKILL.md": SKILL_DOCUMENT, "ab/cd": b"x"}.items()))
        == "skill_package_limit"
    )


def test_zip_body_and_member_count_limits_are_checked_before_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_service.skills.package as package_module

    candidate = archive((("SKILL.md", SKILL_DOCUMENT), ("extra.txt", b"x")))
    monkeypatch.setattr(package_module, "MAX_ARCHIVE_BYTES", len(candidate) - 1)
    assert error_code(lambda: normalize_skill_zip(candidate)) == "skill_package_limit"

    monkeypatch.setattr(package_module, "MAX_ARCHIVE_BYTES", MAX_ARCHIVE_BYTES)
    monkeypatch.setattr(package_module, "MAX_ARCHIVE_MEMBERS", 1)
    assert error_code(lambda: normalize_skill_zip(candidate)) == "skill_package_limit"

    oversized_count = patch_zip_field(candidate, b"PK\x05\x06", 8, 8193)
    oversized_count = patch_zip_field(oversized_count, b"PK\x05\x06", 10, 8193)
    monkeypatch.setattr(package_module, "MAX_ARCHIVE_MEMBERS", MAX_ARCHIVE_MEMBERS)
    assert error_code(lambda: normalize_skill_zip(oversized_count)) == "skill_package_limit"


def test_foundation_stored_zip_has_a_separate_deterministic_encoding_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import a13n_service.skills.package as package_module

    package = normalize_skill_files((("SKILL.md", SKILL_DOCUMENT), ("runbook.md", b"x" * 1024)))
    monkeypatch.setattr(package_module, "MAX_ARCHIVE_BYTES", len(package.archive_bytes) - 1)

    assert error_code(lambda: normalize_skill_zip(package.archive_bytes)) == "skill_package_limit"
    assert normalize_stored_skill_zip(package.archive_bytes) == package


def test_object_key_uses_only_authorized_tenant_identity_and_content_digest() -> None:
    digest = "a" * 64
    organization_id = "org_1234567890abcdef"
    workspace_id = "ws_1234567890abcdef"
    assert skill_package_object_key(organization_id, workspace_id, digest) == (
        f"tenants/{organization_id}/workspaces/{workspace_id}/skills/packages/version-1/" + digest + ".zip"
    )
    with pytest.raises(ValueError):
        skill_package_object_key(organization_id, workspace_id, "A" * 64)
    with pytest.raises(ValueError):
        skill_package_object_key("org/escape", workspace_id, digest)
