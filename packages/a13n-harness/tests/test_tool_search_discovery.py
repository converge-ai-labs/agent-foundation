"""Progressive discovery contracts on the real Harness/ToolManager path."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from a13n_harness.capabilities import CodeActCapability, ProxyToolSearchConfig, ToolSearchCapability, ToolSearchGroup
from pydantic_ai import Tool
from pydantic_ai.toolsets import FunctionToolset

from .test_tool_search import _group, _run

pytestmark = pytest.mark.anyio


def _operation(value: int = 1) -> int:
    return value


@pytest.mark.parametrize("query", ["github create issue", "CREATE create ISSUE", "create-issue", "createIssue"])
async def test_keywords_require_all_words_and_normalize_identifiers(query):
    _, results = await _run(
        (
            _group(
                Tool(_operation, name="create_issue", description="Open a ticket"),
                Tool(_operation, name="delete_issue", description="Remove a ticket"),
                Tool(_operation, name="create_repository", description="Create a repository"),
                group="github",
            ),
        ),
        [("discover_tools", {"query": query})],
    )
    page = results[0].content
    assert page["detail"] == "summary"
    assert [entry["tool"] for entry in page["tools"]] == ["create_issue"]
    assert "parameters_json_schema" not in page["tools"][0]


async def test_exact_identity_precedes_fuzzy_candidates_and_disambiguates_groups():
    sources = {
        group: ToolSearchGroup(
            FunctionToolset(
                [
                    Tool(_operation, name="lookup"),
                    Tool(_operation, name="lookup_extra"),
                ],
                id=group,
            ),
            "Broad domain description",
        )
        for group in ("crm", "docs")
    }
    _, results = await _run(
        (ToolSearchCapability(groups=sources),),
        [
            ("discover_tools", {"query": " LOOKUP "}),
            ("discover_tools", {"query": "lookup", "group": "crm"}),
            ("discover_tools", {"query": "DOCS__LOOKUP"}),
            ("discover_tools", {"query": "lookup", "group": "crm", "detail": "summary"}),
            ("discover_tools", {"query": "lookup", "group": "crm", "offset": 1}),
        ],
    )
    assert results[0].content["detail"] == "summary"
    assert [(x["group"], x["tool"]) for x in results[0].content["tools"]] == [("crm", "lookup"), ("docs", "lookup")]
    assert results[1].content["detail"] == results[2].content["detail"] == "full"
    assert results[2].content["tools"][0]["group"] == "docs"
    assert results[3].content["detail"] == "summary"
    assert results[4].content == {"detail": "summary", "tools": [], "total": 1, "next_offset": None}


async def test_ranking_prefers_name_words_then_whole_words_with_stable_ties():
    _, results = await _run(
        (
            _group(
                Tool(_operation, name="z_issue_create", description="A ticket"),
                Tool(_operation, name="b_create", description="An issue"),
                Tool(_operation, name="a_create", description="An issue"),
                Tool(_operation, name="c_action", description="Create an issue"),
                Tool(_operation, name="a_substring", description="Recreate issues"),
            ),
        ),
        [("discover_tools", {"query": "create issue"}), ("discover_tools", {"query": "no_match"})],
    )
    assert [x["tool"] for x in results[0].content["tools"]] == [
        "z_issue_create",
        "a_create",
        "b_create",
        "c_action",
        "a_substring",
    ]
    assert results[1].content == {"detail": "summary", "tools": [], "total": 0, "next_offset": None}


async def test_summary_uses_full_description_for_search_without_reading_source_guidance():
    reads = []
    effects = []

    def action():
        effects.append(True)

    class Source(FunctionToolset):
        async def get_instructions(self, ctx):
            reads.append(True)
            return "Required source instructions"

    source = Source([Tool(action, description=" 多\n" * 300 + "needle")])
    search = ToolSearchCapability(groups={"docs": ToolSearchGroup(source, "unsearchable_domain_text")})
    _, results = await _run(
        (search,),
        [
            ("discover_tools", {"query": "needle"}),
            ("discover_tools", {"query": "action", "detail": "summary"}),
            ("discover_tools", {"query": "unsearchable_domain_text"}),
        ],
    )
    assert not reads and not effects
    summary = results[0].content["tools"][0]
    assert len(summary["description"]) == 256
    assert summary["description"].endswith("…") and "\n" not in summary["description"]
    assert set(summary) == {"group", "tool", "description", "codeact_eligible"}
    assert results[2].content["total"] == 0
    _, results = await _run((search,), [("describe_tool", {"group": "docs", "tool": "action"})])
    assert reads == [True] and not effects
    assert results[0].content["tool"]["instructions"] == ["Required source instructions"]


@pytest.mark.parametrize("budget", [1024, 32768])
async def test_auto_expansion_budget_counts_complete_utf8_response(budget):
    import a13n_harness.toolsets.tool_search as search

    config = ProxyToolSearchConfig(max_search_bytes=budget)
    threshold = min(8192, budget)
    _, results = await _run(
        (_group(Tool(_operation, name="large", description="多" * 3000), config=config),),
        [("discover_tools", {"query": "large"})],
    )
    assert results[0].content["detail"] == "summary"
    _, baseline = await _run(
        (_group(Tool(_operation, name="small", description="x"), config=config),),
        [("discover_tools", {"query": "small"})],
    )
    size = search._json_bytes(baseline[0].content)
    for extra, expected in [(threshold - size, "full"), (threshold - size + 1, "summary")]:
        _, results = await _run(
            (_group(Tool(_operation, name="small", description="x" * (extra + 1)), config=config),),
            [("discover_tools", {"query": "small"})],
        )
        assert results[0].content["detail"] == expected


@pytest.mark.parametrize("detail,default_count", [("auto", 10), ("summary", 10), ("full", 5)])
async def test_default_page_sizes_and_no_legacy_tool_aliases(detail, default_count):
    def inspect(_step, info):
        assert {x.name for x in info.function_tools} == {"discover_tools", "describe_tool", "call_tool"}
        assert all("proxy" not in x.description.lower() for x in info.function_tools)

    _, results = await _run(
        (_group(*(Tool(_operation, name=f"op_{i:02}") for i in range(12))),),
        [("discover_tools", {"query": "", "detail": detail})],
        inspect=inspect,
    )
    assert len(results[0].content["tools"]) == default_count
    assert results[0].content["next_offset"] == default_count


@pytest.mark.parametrize("detail", ["summary", "full"])
async def test_byte_limited_pages_follow_next_offset_without_losing_entries(detail):
    names = [f"op_{i}" for i in range(6)]
    _, results = await _run(
        (
            _group(
                *(Tool(_operation, name=name, description="多" * 90) for name in names),
                config=ProxyToolSearchConfig(max_search_bytes=1024),
            ),
        ),
        [("discover_tools", {"query": "", "detail": detail, "offset": i, "limit": 6}) for i in range(7)],
    )
    pages = [result.content for result in results]
    assert all(len(json.dumps(page, ensure_ascii=False).encode()) <= 1024 for page in pages)
    assert 0 < len(pages[0]["tools"]) < 6
    offset = 0
    found = []
    while offset is not None:
        page = pages[offset]
        assert page["total"] == 6 and page["detail"] == detail
        found.extend(entry["tool"] for entry in page["tools"])
        if detail == "full":
            assert all(
                entry["parameters_json_schema"]["properties"]["value"]["type"] == "integer" for entry in page["tools"]
            )
        offset = page["next_offset"]
    assert found == names
    assert pages[6]["tools"] == [] and pages[6]["next_offset"] is None


async def test_explicit_full_and_describe_fail_for_oversized_definitions_but_summary_survives():
    _, results = await _run(
        (
            _group(
                Tool(_operation, name="giant", description="x" * 2000),
                config=ProxyToolSearchConfig(max_search_bytes=1024),
            ),
        ),
        [
            ("discover_tools", {"query": "giant"}),
            ("discover_tools", {"query": "giant", "detail": "full"}),
            ("describe_tool", {"group": "crm", "tool": "giant"}),
        ],
    )
    assert results[0].content["detail"] == "summary"
    for result in results[1:]:
        assert result.outcome == "failed"
        assert "no partial schema" in result.content


@pytest.mark.parametrize("codeact", [False, True])
async def test_describe_custom_name_uses_current_schema_without_discovery_or_execution(codeact):
    effects = []

    def action(value: int):
        effects.append(value)
        return value

    async def prepare(ctx, definition):
        schema = {**definition.parameters_json_schema, "description": f"step-{ctx.run_step}"}
        return replace(definition, parameters_json_schema=schema)

    config = ProxyToolSearchConfig(search_name="find_ops", describe_name="read_op", call_name="invoke_op")
    calls = [("read_op", {"group": "crm", "tool": "action"})] * 2
    if codeact:
        calls = [("run_code", {"code": "await read_op(group='crm', tool='action')"})] * 2
    _, results = await _run(
        (_group(Tool(action, prepare=prepare), config=config), *((CodeActCapability(),) if codeact else ())),
        calls,
    )
    assert not effects
    if not codeact:
        first, second = [r.content["tool"] for r in results]
        assert first["parameters_json_schema"]["description"] != second["parameters_json_schema"]["description"]
        assert first["instructions"] == ["Use exact customer IDs."]
    else:
        assert all("parameters_json_schema" in str(r.content) and "step-" in str(r.content) for r in results)


@pytest.mark.parametrize(
    "names",
    [
        {"search_name": "same", "describe_name": "same"},
        {"call_name": "same", "describe_name": "same"},
        {"describe_name": "invalid name"},
    ],
)
async def test_describe_control_name_validation(names):
    with pytest.raises(ValueError):
        ProxyToolSearchConfig(**names)


async def test_nonempty_punctuation_query_is_not_browsing_and_describe_checks_availability():
    from pydantic_ai.messages import RetryPromptPart

    result, _ = await _run(
        (_group(_operation),),
        [
            ("discover_tools", {"query": "?!__"}),
            ("describe_tool", {"group": "crm", "tool": "missing"}),
        ],
    )
    retries = [p.content for m in result.all_messages() for p in m.parts if isinstance(p, RetryPromptPart)]
    assert len(retries) == 2
    assert "operation keywords" in retries[0]
    assert "unavailable" in retries[1]
