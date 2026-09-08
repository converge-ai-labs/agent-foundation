"""Non-importing structural validation for one standard Plugin Wheel."""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from anyio import CapacityLimiter, to_thread
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .domain import PLUGIN_KEY_PATTERN
from .errors import PluginError, plugin_artifact_invalid, plugin_artifact_limit

HARNESS_PLUGIN_ENTRY_POINT_GROUP = "a13n_harness.plugins"
_PLUGIN_KEY = re.compile(PLUGIN_KEY_PATTERN)
_IMPORT_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_METADATA_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class InspectedPluginWheel:
    plugin_key: str
    entry_point_target: str
    distribution_name: str
    version: str
    top_level_package: str
    requires_dist: tuple[str, ...]
    requires_python: str | None
    wheel_tags: tuple[str, ...]
    root_is_purelib: bool


@dataclass(frozen=True, slots=True)
class InspectedDistributionWheel:
    distribution_name: str
    version: str
    requires_dist: tuple[str, ...]
    requires_python: str | None
    wheel_tags: tuple[str, ...]
    root_is_purelib: bool


async def inspect_plugin_wheel(
    path: Path,
    *,
    max_expanded_bytes: int,
    max_members: int,
    limiter: CapacityLimiter | None = None,
) -> InspectedPluginWheel:
    try:
        return await to_thread.run_sync(
            _inspect,
            path,
            max_expanded_bytes,
            max_members,
            limiter=limiter,
        )
    except PluginError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeError) as error:
        raise plugin_artifact_invalid("invalid_archive") from error


async def inspect_distribution_wheel(
    path: Path,
    *,
    max_expanded_bytes: int,
    max_members: int,
    limiter: CapacityLimiter | None = None,
) -> InspectedDistributionWheel:
    try:
        return await to_thread.run_sync(
            _inspect_distribution,
            path,
            max_expanded_bytes,
            max_members,
            limiter=limiter,
        )
    except PluginError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, UnicodeError) as error:
        raise plugin_artifact_invalid("invalid_archive") from error


def _inspect(path: Path, max_expanded_bytes: int, max_members: int) -> InspectedPluginWheel:
    with zipfile.ZipFile(path) as archive:
        by_name, dist_info = _inspect_archive(archive, max_expanded_bytes, max_members)
        metadata_name = f"{dist_info}/METADATA"
        wheel_name = f"{dist_info}/WHEEL"
        entries_name = f"{dist_info}/entry_points.txt"
        record_name = f"{dist_info}/RECORD"
        for required in (metadata_name, wheel_name, entries_name, record_name):
            if required not in by_name:
                raise plugin_artifact_invalid("wheel_metadata_missing")

        metadata = _read_bounded(archive, metadata_name)
        distribution_name, version, requires_dist, requires_python = _parse_metadata(metadata)
        root_is_purelib, wheel_tags = _parse_wheel(_read_bounded(archive, wheel_name))
        plugin_key, entry_target = _parse_entry_points(_read_bounded(archive, entries_name))
        top_level_package = _top_level_package(by_name, dist_info)
        if entry_target.split(":", 1)[0].split(".", 1)[0] != top_level_package:
            raise plugin_artifact_invalid("entry_point_outside_package")
        _validate_record(archive, by_name, record_name)
        return InspectedPluginWheel(
            plugin_key=plugin_key,
            entry_point_target=entry_target,
            distribution_name=distribution_name,
            version=version,
            top_level_package=top_level_package,
            requires_dist=requires_dist,
            requires_python=requires_python,
            wheel_tags=wheel_tags,
            root_is_purelib=root_is_purelib,
        )


def _inspect_distribution(
    path: Path,
    max_expanded_bytes: int,
    max_members: int,
) -> InspectedDistributionWheel:
    with zipfile.ZipFile(path) as archive:
        by_name, dist_info = _inspect_archive(archive, max_expanded_bytes, max_members)
        metadata_name = f"{dist_info}/METADATA"
        wheel_name = f"{dist_info}/WHEEL"
        entries_name = f"{dist_info}/entry_points.txt"
        record_name = f"{dist_info}/RECORD"
        for required in (metadata_name, wheel_name, record_name):
            if required not in by_name:
                raise plugin_artifact_invalid("wheel_metadata_missing")
        distribution_name, version, requires_dist, requires_python = _parse_metadata(
            _read_bounded(archive, metadata_name)
        )
        root_is_purelib, wheel_tags = _parse_wheel(_read_bounded(archive, wheel_name))
        if entries_name in by_name:
            _reject_reserved_entry_points(_read_bounded(archive, entries_name))
        _validate_record(archive, by_name, record_name)
        return InspectedDistributionWheel(
            distribution_name=distribution_name,
            version=version,
            requires_dist=requires_dist,
            requires_python=requires_python,
            wheel_tags=wheel_tags,
            root_is_purelib=root_is_purelib,
        )


def _inspect_archive(
    archive: zipfile.ZipFile,
    max_expanded_bytes: int,
    max_members: int,
) -> tuple[dict[str, zipfile.ZipInfo], str]:
    infos = archive.infolist()
    if not infos or len(infos) > max_members:
        raise plugin_artifact_limit()
    by_name: dict[str, zipfile.ZipInfo] = {}
    expanded = 0
    for info in infos:
        _validate_member(info)
        if info.filename in by_name:
            raise plugin_artifact_invalid("duplicate_member")
        by_name[info.filename] = info
        expanded += info.file_size
        if expanded > max_expanded_bytes:
            raise plugin_artifact_limit()
    dist_info_dirs = {
        PurePosixPath(name).parts[0]
        for name in by_name
        if PurePosixPath(name).parts and PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if len(dist_info_dirs) != 1:
        raise plugin_artifact_invalid("dist_info_count")
    return by_name, next(iter(dist_info_dirs))


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or info.flag_bits & 0x1
    ):
        raise plugin_artifact_invalid("unsafe_archive_member")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise plugin_artifact_invalid("linked_archive_member")
    if info.file_size < 0 or info.compress_size < 0:
        raise plugin_artifact_invalid("invalid_archive_member")


def _read_bounded(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.is_dir() or info.file_size > _MAX_METADATA_BYTES:
        raise plugin_artifact_invalid("wheel_metadata_invalid")
    return archive.read(info)


def _parse_metadata(raw: bytes) -> tuple[str, str, tuple[str, ...], str | None]:
    message = BytesParser().parsebytes(raw)
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        raise plugin_artifact_invalid("distribution_metadata_invalid")
    distribution_name = canonicalize_name(names[0])
    try:
        version = str(Version(versions[0]))
    except InvalidVersion as error:
        raise plugin_artifact_invalid("distribution_version_invalid") from error
    requirements: list[str] = []
    try:
        for raw_requirement in message.get_all("Requires-Dist", []):
            requirement = Requirement(raw_requirement)
            if requirement.url is not None:
                raise plugin_artifact_invalid("direct_requirement_unsupported")
            requirements.append(str(requirement))
    except InvalidRequirement as error:
        raise plugin_artifact_invalid("requirement_invalid") from error
    requires_python = message.get("Requires-Python")
    if requires_python is not None:
        try:
            requires_python = str(SpecifierSet(requires_python))
        except InvalidSpecifier as error:
            raise plugin_artifact_invalid("requires_python_invalid") from error
    return distribution_name, version, tuple(requirements), requires_python


def _parse_wheel(raw: bytes) -> tuple[bool, tuple[str, ...]]:
    message = BytesParser().parsebytes(raw)
    pure_values = message.get_all("Root-Is-Purelib", [])
    tags = tuple(message.get_all("Tag", []))
    if len(pure_values) != 1 or pure_values[0].lower() not in {"true", "false"} or not tags:
        raise plugin_artifact_invalid("wheel_metadata_invalid")
    try:
        for value in tags:
            if not parse_tag(value):
                raise ValueError
    except (ValueError, TypeError) as error:
        raise plugin_artifact_invalid("wheel_tag_invalid") from error
    return pure_values[0].lower() == "true", tags


def _parse_entry_points(raw: bytes) -> tuple[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = _identity_option
    try:
        parser.read_string(raw.decode("utf-8"))
    except (configparser.Error, UnicodeError) as error:
        raise plugin_artifact_invalid("entry_points_invalid") from error
    forbidden = tuple(
        section
        for section in parser.sections()
        if section != HARNESS_PLUGIN_ENTRY_POINT_GROUP and section.startswith(("a13n_", "agent_foundation."))
    )
    entries = (
        tuple(parser.items(HARNESS_PLUGIN_ENTRY_POINT_GROUP))
        if parser.has_section(HARNESS_PLUGIN_ENTRY_POINT_GROUP)
        else ()
    )
    if forbidden or len(entries) != 1:
        raise plugin_artifact_invalid("plugin_entry_point_count")
    plugin_key, target = entries[0]
    if _PLUGIN_KEY.fullmatch(plugin_key) is None:
        raise plugin_artifact_invalid("plugin_key_invalid")
    module, separator, attribute = target.partition(":")
    if (
        separator != ":"
        or "[" in attribute
        or not all(_IMPORT_PART.fullmatch(part) for part in module.split("."))
        or not all(_IMPORT_PART.fullmatch(part) for part in attribute.split("."))
    ):
        raise plugin_artifact_invalid("entry_point_target_invalid")
    return plugin_key, target


def _reject_reserved_entry_points(raw: bytes) -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = _identity_option
    try:
        parser.read_string(raw.decode("utf-8"))
    except (configparser.Error, UnicodeError) as error:
        raise plugin_artifact_invalid("entry_points_invalid") from error
    if any(section.startswith(("a13n_", "agent_foundation.")) for section in parser.sections()):
        raise plugin_artifact_invalid("dependency_extension_entry_point")


def _identity_option(optionstr: str) -> str:
    return optionstr


def _top_level_package(by_name: dict[str, zipfile.ZipInfo], dist_info: str) -> str:
    packages: set[str] = set()
    for name, info in by_name.items():
        if info.is_dir():
            continue
        parts = PurePosixPath(name).parts
        if not parts or parts[0] == dist_info or parts[0].endswith(".data") or not name.endswith((".py", ".pyi")):
            continue
        candidate = parts[0][:-3] if len(parts) == 1 and parts[0].endswith(".py") else parts[0]
        if _IMPORT_PART.fullmatch(candidate):
            packages.add(candidate)
    if len(packages) != 1:
        raise plugin_artifact_invalid("top_level_package_count")
    return next(iter(packages))


def _validate_record(
    archive: zipfile.ZipFile,
    by_name: dict[str, zipfile.ZipInfo],
    record_name: str,
) -> None:
    try:
        rows = tuple(csv.reader(io.StringIO(_read_bounded(archive, record_name).decode("utf-8"))))
    except (csv.Error, UnicodeError) as error:
        raise plugin_artifact_invalid("record_invalid") from error
    entries: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in entries:
            raise plugin_artifact_invalid("record_invalid")
        entries[row[0]] = (row[1], row[2])
    files = {name for name, info in by_name.items() if not info.is_dir()}
    if set(entries) != files:
        raise plugin_artifact_invalid("record_incomplete")
    for name, (encoded_hash, encoded_size) in entries.items():
        if name == record_name:
            if encoded_hash or encoded_size:
                raise plugin_artifact_invalid("record_self_hash")
            continue
        try:
            size = int(encoded_size)
        except ValueError as error:
            raise plugin_artifact_invalid("record_size_invalid") from error
        data = archive.read(by_name[name])
        if size != len(data):
            raise plugin_artifact_invalid("record_size_mismatch")
        algorithm, separator, expected = encoded_hash.partition("=")
        if separator != "=" or algorithm not in {"sha256", "sha384", "sha512"}:
            raise plugin_artifact_invalid("record_hash_invalid")
        actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b"=").decode()
        if actual != expected:
            raise plugin_artifact_invalid("record_hash_mismatch")
