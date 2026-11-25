"""Run-frozen SEP-2640 manifests and confined lazy reads over an entered MCP client."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from a13n_logging import get_logger
from fastmcp.skills import Skill, SkillResource, SkillsClientExtension
from mcp.types import BlobResourceContents, TextResourceContents
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import AnyUrl, BaseModel, ConfigDict, JsonValue
from pydantic_ai import RunContext

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError

if TYPE_CHECKING:
    from fastmcp import Client

    from a13n_harness.mcp import MCPServer

MCP_SKILL_READ_TOOL = "read_mcp_skill"
DEFAULT_MCP_SKILL_FILE_BYTES = 256 * 1024
_MAX_SKILL_RESOURCES = 512
MAX_MCP_SKILL_BYTES = 16 * 1024 * 1024
_MAX_PAGES = 250


class _MCPSkillReadError(Exception):
    """A bounded model-facing read failure."""


class MCPSkillCatalogItem(BaseModel):
    """Detached metadata for a selected remote skill; its URI is not a read capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    source_id: str
    uri: str


@dataclass(frozen=True, slots=True)
class _MCPSkill:
    item: MCPSkillCatalogItem
    client: Client[Any]
    files: dict[str, SkillResource]
    frontmatter: dict[str, JsonValue]


def _safe_relative_path(path: str) -> bool:
    return all(
        segment not in {"", ".", ".."}
        and not any(char in "\\%:?#" or ord(char) < 32 or ord(char) == 127 for char in segment)
        for segment in path.split("/")
    )


def _resource_path(uri: str, root: str) -> str:
    # Check the exact advertised spelling before using AnyUrl, which can normalize
    # origins or collapse path segments on the wire. Decode only for the local key.
    parsed = urlsplit(uri)
    if not uri.startswith(root) or parsed.username is not None or "?" in uri or "#" in uri or str(AnyUrl(uri)) != uri:
        raise ValueError("Skill resource URI is outside its canonical directory")
    decoded = [unquote(segment, errors="strict") for segment in parsed.path.split("/")[1:]]
    if not parsed.path.startswith("/") or any("/" in part or not _safe_relative_path(part) for part in decoded):
        raise ValueError("Skill resource URI has an ambiguous path segment")
    relative = unquote(uri[len(root) :], errors="strict")
    if not _safe_relative_path(relative):
        raise ValueError("Skill resource URI has an invalid relative path")
    return relative


def _bind_skill(skill: Skill, source_id: str, client: Client[Any]) -> _MCPSkill | None:
    if skill.resources == "dynamic":
        return None
    if len(skill.resources) > _MAX_SKILL_RESOURCES or sum(r.size for r in skill.resources) > MAX_MCP_SKILL_BYTES:
        raise ValueError("MCP skill exceeds SEP-2640 manifest limits")
    root, _, filename = skill.uri.rpartition("/")
    if filename != "SKILL.md":
        raise ValueError("Expected the canonical SKILL.md URI")
    files: dict[str, SkillResource] = {}
    for resource in skill.resources:
        path = _resource_path(resource.uri, root + "/")
        if path in files:
            raise ValueError("MCP skill manifest contains ambiguous file paths")
        files[path] = resource
    return _MCPSkill(
        MCPSkillCatalogItem(
            name=skill.frontmatter.name,
            description=skill.frontmatter.description,
            source_id=source_id,
            uri=skill.uri,
        ),
        client,
        files,
        skill.frontmatter.root,
    )


async def discover_mcp_skills(
    server: MCPServer, ctx: RunContext[AgentContext], *, max_skills: int
) -> tuple[_MCPSkill, ...]:
    client = (await server._started(ctx)).client
    if client is None or client.protocol_version not in MODERN_PROTOCOL_VERSIONS:
        return ()
    caps = client.server_capabilities
    if caps is None or not caps.extensions or SkillsClientExtension.identifier not in caps.extensions:
        return ()
    source_id = f"mcp:{server.id}"
    try:
        return await _list_mcp_skills(client, source_id=source_id, max_skills=max_skills)
    except DefinitionError:
        raise
    except Exception as exc:
        if server._on_error != "skip":
            raise DefinitionError(
                "MCP skill discovery failed.", code="skill_source_failed", details={"source_id": source_id}
            ) from exc
        get_logger(__name__).warning(
            "MCP skill discovery skipped", extra={"source_id": source_id, "error_type": type(exc).__name__}
        )
        return ()


async def _list_mcp_skills(client: Client[Any], *, source_id: str, max_skills: int) -> tuple[_MCPSkill, ...]:
    found: list[_MCPSkill] = []
    cursor: str | None = None
    seen: set[str] = set()
    count = 0
    for _ in range(_MAX_PAGES):
        page = await client.list_skills_mcp(cursor=cursor, cache_mode="bypass")
        count += len(page.skills)
        if count > max_skills:
            raise DefinitionError(
                "A selected skill source exceeds the configured catalog size.",
                code="skill_catalog_too_large",
                details={"source_id": source_id},
            )
        for entry in page.skills:
            try:
                skill = _bind_skill(entry, source_id, client)
            except ValueError:
                get_logger(__name__).warning("Invalid MCP skill manifest skipped", extra={"source_id": source_id})
                continue
            if skill is not None:
                found.append(skill)
        cursor = page.next_cursor
        if not cursor:
            return tuple(found)
        if cursor in seen:
            break
        seen.add(cursor)
    raise ValueError("MCP skill pagination did not terminate.")


def same_frontmatter(value: object, expected: JsonValue) -> bool:
    # Compare only against the advertised JSON tree. Do not expand YAML aliases or
    # accept Python's bool/int equality when checking a remote frontmatter field.
    if isinstance(expected, dict):
        return (
            isinstance(value, dict)
            and value.keys() == expected.keys()
            and all(same_frontmatter(value[key], item) for key, item in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(value, list)
            and len(value) == len(expected)
            and all(same_frontmatter(left, right) for left, right in zip(value, expected, strict=True))
        )
    return type(value) is type(expected) and value == expected


async def read_mcp_skill_text(skill: _MCPSkill, path: str, *, max_bytes: int) -> str:
    file = skill.files.get(path) if _safe_relative_path(path) else None
    if file is None:
        raise _MCPSkillReadError("Use a relative file path listed by the selected MCP skill.")
    limit_error = "MCP skill file exceeds the configured byte limit."
    if file.size > max_bytes:
        raise _MCPSkillReadError(limit_error)
    try:
        # FastMCP's convenience reader follows interactive sampling/elicitation
        # rounds. Skill reads must not borrow those permissions from tool calls.
        result = await skill.client._await_with_session_monitoring(skill.client.session.read_resource(file.uri))
        contents = result.contents
    except Exception:
        raise _MCPSkillReadError("The selected MCP skill file is unavailable.") from None
    if len(contents) != 1 or str(contents[0].uri) != file.uri:
        raise _MCPSkillReadError("The MCP server did not return the requested skill file.")
    content = contents[0]
    try:
        if isinstance(content, TextResourceContents):
            if len(content.text) > max_bytes:
                raise _MCPSkillReadError(limit_error)
            raw = content.text.encode("utf-8")
        elif isinstance(content, BlobResourceContents):
            if len(content.blob) > 4 * ((max_bytes + 2) // 3):
                raise _MCPSkillReadError(limit_error)
            raw = b64decode(content.blob, validate=True)
        else:
            raise _MCPSkillReadError("The MCP server did not return file content.")
        if len(raw) > max_bytes:
            raise _MCPSkillReadError(limit_error)
        if len(raw) != file.size or f"sha256:{sha256(raw).hexdigest()}" != file.digest:
            raise _MCPSkillReadError("MCP skill file differs from the frozen manifest.")
        return raw.decode("utf-8")
    except (Base64Error, UnicodeError):
        raise _MCPSkillReadError("The selected MCP skill file is not valid UTF-8 text.") from None
