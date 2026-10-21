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
async def test_keywords_recall_any_word_and_rank_coverage_first(query):
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
    assert [entry["tool"] for entry in page["tools"]] == ["create_issue", "create_repository", "delete_issue"]
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
    assert [(x["group"], x["tool"]) for x in results[0].content["tools"]] == [
        ("crm", "lookup"),
        ("docs", "lookup"),
        ("crm", "lookup_extra"),
        ("docs", "lookup_extra"),
    ]
    assert all(result.content["detail"] == "summary" for result in results)
    assert [(x["group"], x["tool"]) for x in results[1].content["tools"]] == [
        ("crm", "lookup"),
        ("crm", "lookup_extra"),
    ]
    assert [(x["group"], x["tool"]) for x in results[2].content["tools"]] == [
        ("docs", "lookup"),
        ("docs", "lookup_extra"),
        ("crm", "lookup"),
        ("crm", "lookup_extra"),
    ]
    assert results[4].content["total"] == 2
    assert [x["tool"] for x in results[4].content["tools"]] == ["lookup_extra"]


async def test_ranking_prefers_name_words_then_whole_words_with_stable_ties():
    _, results = await _run(
        (
            _group(
                Tool(_operation, name="z_issue_create", description="A ticket"),
                Tool(_operation, name="b_create", description="An issue"),
                Tool(_operation, name="a_create", description="An issue"),
                Tool(_operation, name="c_action", description="Create an issue"),
                Tool(_operation, name="a_substring", description="Recreate issues"),
                Tool(_operation, name="create_only", description="Partial match"),
            ),
        ),
        [("discover_tools", {"query": "create issue"}), ("discover_tools", {"query": "absentkeyword"})],
    )
    assert [x["tool"] for x in results[0].content["tools"]] == [
        "z_issue_create",
        "a_create",
        "b_create",
        "c_action",
        "a_substring",
        "create_only",
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


@pytest.mark.parametrize("limit", [1, 10])
async def test_exact_priority_preserves_candidates_and_auto_uses_whole_set(limit):
    reads = []

    class Source(FunctionToolset):
        async def get_instructions(self, ctx):
            reads.append(True)
            return "Source guidance"

    related = ["payment_lookup", "refund_payment", "payment_history", "payment_status"]
    source = Source([Tool(_operation, name=name, description="多" * 90) for name in ["payment", *related]])
    config = ProxyToolSearchConfig(max_search_bytes=1024)
    search = ToolSearchCapability(groups={"crm": ToolSearchGroup(source, "Operations")}, config=config)
    _, results = await _run(
        (search,),
        [("discover_tools", {"query": "payment", "offset": i, "limit": limit}) for i in range(5)],
    )
    pages = [r.content for r in results]
    assert not reads
    offset = 0
    found = []
    while offset is not None:
        page = pages[offset]
        assert page["detail"] == "summary" and page["total"] == 5
        found.extend(x["tool"] for x in page["tools"])
        offset = page["next_offset"]
    assert found == ["payment", *sorted(related)]
    _, before = await _run(
        (_group(*(Tool(_operation, name=name) for name in related)),),
        [("discover_tools", {"query": "payment"})],
    )
    assert {x["tool"] for x in before[0].content["tools"]} == set(found[1:])


@pytest.mark.parametrize("query", ["getHTTP", "GETHTTP", "gethttp", "crm__GETHTTP"])
async def test_case_insensitive_exact_and_raw_substrings_survive_camel_boundaries(query):
    _, results = await _run(
        (_group(Tool(_operation, name="getHTTP"), Tool(_operation, name="getHTTPStatus")),),
        [("discover_tools", {"query": query})],
    )
    assert [x["tool"] for x in results[0].content["tools"]] == ["getHTTP", "getHTTPStatus"]
    assert results[0].content["detail"] == "summary"


async def test_canonical_local_collision_and_case_variants_keep_distinct_identities():
    search = ToolSearchCapability(
        groups={
            "crm": ToolSearchGroup(
                FunctionToolset([Tool(_operation, name="lookup"), Tool(_operation, name="LOOKUP")], id="crm"), "CRM"
            ),
            "ops": ToolSearchGroup(FunctionToolset([Tool(_operation, name="crm__lookup")], id="ops"), "Operations"),
        }
    )
    _, results = await _run(
        (search,),
        [
            ("discover_tools", {"query": "crm__lookup"}),
            ("discover_tools", {"query": "crm__lookup", "group": "ops"}),
        ],
    )
    assert {(x["group"], x["tool"]) for x in results[0].content["tools"]} == {
        ("crm", "lookup"),
        ("crm", "LOOKUP"),
        ("ops", "crm__lookup"),
    }
    assert results[0].content["detail"] == "summary"
    assert results[1].content["detail"] == "full" and results[1].content["total"] == 1


async def test_punctuation_only_exact_name_is_valid():
    _, results = await _run(
        (_group(Tool(_operation, name="__"), Tool(_operation, name="other")),),
        [("discover_tools", {"query": " __ "})],
    )
    assert results[0].content["detail"] == "full"
    assert [x["tool"] for x in results[0].content["tools"]] == ["__"]


async def test_parameter_search_traverses_current_input_schema_only():
    schema = {
        "type": "object",
        "properties": {
            "payload": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]},
            "mapping": {
                "type": "object",
                "additionalProperties": {"type": "string", "description": "warehouse locator"},
            },
            "pair": {"type": "array", "prefixItems": [{"description": "fingerprint"}, {"type": "integer"}]},
        },
        "$defs": {
            "Node": {
                "properties": {
                    "customerId": {"type": "string", "description": "Purchaser reference"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
                "allOf": [{"description": "nested guidance"}],
            }
        },
        "description": "Root guidance",
        "default": {"description": "defaultsecret"},
        "examples": [{"properties": {"examplesecret": {"description": "examplesecret"}}}],
        "title": "titlesecret",
        "$ref": "https://invalid.example/remotesecret",
    }
    original = json.dumps(schema, sort_keys=True)
    reads = []

    async def prepare(ctx, definition):
        return replace(definition, parameters_json_schema=schema, return_schema={"description": "returnsecret"})

    class Source(FunctionToolset):
        async def get_instructions(self, ctx):
            reads.append(True)
            return "instructionsecret"

    source = Source([Tool(_operation, name="action", description="Execute", prepare=prepare)])
    search = ToolSearchCapability(groups={"ops": ToolSearchGroup(source, "groupsecret")})
    hits = ["CUSTOMERID", "purchas", "warehouse", "fingerprint", "nested", "root", "payload"]
    misses = [
        "defaultsecret",
        "examplesecret",
        "returnsecret",
        "instructionsecret",
        "titlesecret",
        "remotesecret",
        "groupsecret",
        "Node",
    ]
    _, results = await _run((search,), [("discover_tools", {"query": q}) for q in [*hits, *misses]])
    assert [r.content["total"] for r in results] == [1] * len(hits) + [0] * len(misses)
    assert all(r.content["detail"] == "summary" for r in results)
    assert not reads and json.dumps(schema, sort_keys=True) == original


async def test_parameter_search_uses_each_steps_prepared_definition():
    steps = []

    async def prepare(ctx, definition):
        steps.append(ctx.run_step)
        return replace(
            definition,
            parameters_json_schema={"type": "object", "properties": {}, "description": f"step{ctx.run_step}"},
        )

    _, results = await _run(
        (_group(Tool(_operation, prepare=prepare)),),
        [("discover_tools", {"query": "step1"}), ("discover_tools", {"query": "step1"})],
    )
    assert steps[:2] == [1, 2]
    assert [r.content["total"] for r in results] == [1, 0]


async def test_field_priority_counts_each_keyword_once_and_keeps_stable_ties():
    async def parameter_name(ctx, definition):
        return replace(definition, parameters_json_schema={"properties": {"needle": {"type": "string"}}})

    async def parameter_description(ctx, definition):
        return replace(definition, parameters_json_schema={"properties": {"value": {"description": "needle " * 30}}})

    search = ToolSearchCapability(
        groups={
            "ops": ToolSearchGroup(
                FunctionToolset(
                    [
                        Tool(_operation, name="z_needle"),
                        Tool(_operation, name="b_description", description="needle " * 30, prepare=parameter_name),
                        Tool(_operation, name="a_description", description="needle"),
                        Tool(_operation, name="c_parameter", prepare=parameter_name),
                        Tool(_operation, name="d_parameter", prepare=parameter_description),
                        Tool(_operation, name="a_needles"),
                    ],
                    id="ops",
                ),
                "Operations",
            ),
            "needle": ToolSearchGroup(FunctionToolset([Tool(_operation, name="action")], id="needle"), "Domain"),
        }
    )
    _, results = await _run((search,), [("discover_tools", {"query": q}) for q in ["needle", "needle NEEDLE needle"]])
    expected = ["z_needle", "a_description", "b_description", "action", "c_parameter", "d_parameter", "a_needles"]
    assert all([x["tool"] for x in r.content["tools"]] == expected for r in results)


def test_config_preserves_existing_positional_arguments():
    config = ProxyToolSearchConfig("find", "invoke", 20, 4096)
    assert (
        config.search_name,
        config.call_name,
        config.max_results,
        config.max_search_bytes,
        config.describe_name,
    ) == ("find", "invoke", 20, 4096, "describe_tool")
