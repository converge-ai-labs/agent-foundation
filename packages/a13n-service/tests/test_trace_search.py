"""Provider query execution and uncached public trace search semantics."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx2
import pytest
from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import SpanQuery, langfuse
from a13n_service.providers.traces.logfire import _search_sql
from a13n_service.providers.traces.search import SearchQuery
from a13n_service.runs import trace_search

from . import test_traces
from .mcp_support import call, client, key, rpc
from .test_traces import Backend, langfuse_row, logfire_row

backend = test_traces.backend

pytestmark = pytest.mark.anyio
START = datetime(2026, 9, 23, 10, tzinfo=UTC)
PARAMS = {"started_after": START.isoformat(), "started_before": (START + timedelta(hours=1)).isoformat()}


def scope(service: SimpleNamespace) -> dict[str, str]:
    return correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)


def root(service: SimpleNamespace, number: int, seconds: int | None = 10, **attributes: str) -> dict:
    row = langfuse_row(f"{number:016x}", {"attributes": {**scope(service), **attributes}}, trace_id=f"{number:032x}")
    row["endTime"] = (START + timedelta(seconds=seconds)).isoformat() if seconds is not None else None
    return row


def metric(number: int, cost: str, category: str = "total") -> dict:
    return {"traceId": f"{number:032x}", "costType": category, "sum_costByType": cost}


def roots_page(*rows: dict, cursor: str | None = None) -> httpx2.Response:
    return httpx2.Response(200, json={"data": rows, "meta": {"cursor": cursor}})


def metrics_page(*rows: dict) -> httpx2.Response:
    return httpx2.Response(200, json={"data": rows})


def configure(service: SimpleNamespace, backend: Backend, kind: str = "langfuse") -> None:
    service.app.state.runtime = replace(service.runtime, traces=getattr(backend, kind)())


async def test_langfuse_cost_ranking_recollects_on_each_page(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend)
    foreign = root(service, 9, 999)
    foreign["metadata"] = {}
    for _ in range(2):
        backend.answer(
            roots_page(root(service, 1), foreign, cursor="next"),
            roots_page(root(service, 2), root(service, 3)),
            metrics_page(metric(1, "0.1"), metric(2, "0.1"), metric(3, "8.1"), metric(3, "5", "input")),
        )
    params = {**PARAMS, "order_by": "cost", "limit": 1}
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    body = first.json()
    assert "snapshot" not in body
    assert body["total"] == 3
    assert body["items"][0]["root"]["trace_id"] == f"{3:032x}"
    assert body["items"][0]["cost_usd"] == "8.1"  # includes root already; never add it twice
    assert body["items"][0]["root"]["input"] is None
    assert len(backend.requests) == 3
    request = json.loads(backend.requests[-1].url.params["query"])
    assert request["dimensions"] == [{"field": "traceId"}, {"field": "costType"}]
    assert request["metrics"] == [{"measure": "costByType", "aggregation": "sum"}]
    assert request["filters"][0]["value"] == [f"{i:032x}" for i in (1, 2, 3)]
    assert datetime.fromisoformat(request["toTimestamp"]) > START + timedelta(hours=2)
    second = await service.client.get(f"{service.api}/traces/search", params={**params, "cursor": body["next_cursor"]})
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["root"]["trace_id"] == f"{1:032x}"
    assert len(backend.requests) == 6
    assert json.loads(backend.requests[-1].url.params["query"])["filters"][0]["value"] == request["filters"][0]["value"]
    for options in (
        {"direction": "asc"},
        {"min_cost_usd": "1"},
        {"order_by": "duration"},
        {"started_after": (START - timedelta(hours=1)).isoformat()},
        {"attribute": "stage:prod"},
    ):
        changed = await service.client.get(
            f"{service.api}/traces/search", params={**params, **options, "cursor": body["next_cursor"]}
        )
        assert changed.status_code == 400
    assert len(backend.requests) == 6


@pytest.mark.parametrize("direction, expected", [("desc", [3, 2, 1, 4]), ("asc", [1, 2, 3, 4])])
async def test_duration_ranking_only_fetches_each_pages_costs(
    service: SimpleNamespace, backend: Backend, direction: str, expected: list[int]
) -> None:
    configure(service, backend)
    params = {**PARAMS, "order_by": "duration", "direction": direction, "limit": 2}
    cursor = None
    for page_ids in (expected[:2], expected[2:]):
        backend.answer(
            roots_page(root(service, 1, 0), root(service, 2, 20), cursor="last"),
            roots_page(root(service, 3, 30), root(service, 4, None)),
            metrics_page(*(metric(i, "0" if i == 1 else "0.123456789012345") for i in page_ids if i != 4)),
        )
        result = await service.client.get(
            f"{service.api}/traces/search", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["total"] == 4
        assert [int(item["root"]["trace_id"], 16) for item in body["items"]] == page_ids
        assert [item["cost_usd"] for item in body["items"]] == [
            None if i == 4 else "0" if i == 1 else "0.123456789012345" for i in page_ids
        ]
        ids = json.loads(backend.requests[-1].url.params["query"])["filters"][0]["value"]
        assert ids == [f"{i:032x}" for i in page_ids]
        cursor = body["next_cursor"]
    assert cursor is None
    assert len(backend.requests) == 6


@pytest.mark.parametrize("bounds", [{"min_cost_usd": "0"}, {"max_cost_usd": "0"}])
async def test_duration_ranking_with_cost_bounds_still_aggregates_all_candidates(
    service: SimpleNamespace, backend: Backend, bounds: dict
) -> None:
    configure(service, backend)
    backend.answer(
        roots_page(root(service, 1, 30), root(service, 2, 20), root(service, 3, 10)),
        metrics_page(metric(2, "0"), metric(3, "0")),
    )
    result = await service.client.get(
        f"{service.api}/traces/search", params={**PARAMS, "order_by": "duration", "limit": 1, **bounds}
    )
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 2
    assert result.json()["items"][0]["root"]["trace_id"] == f"{2:032x}"
    assert json.loads(backend.requests[-1].url.params["query"])["filters"][0]["value"] == [
        f"{i:032x}" for i in (1, 2, 3)
    ]


async def test_duration_bounds_select_before_display_costs_and_skip_empty_page(
    service: SimpleNamespace, backend: Backend
) -> None:
    configure(service, backend)
    params = {**PARAMS, "order_by": "duration", "min_duration_seconds": "20", "max_duration_seconds": "30", "limit": 1}
    backend.answer(
        roots_page(root(service, 1, 10), root(service, 2, 20), root(service, 3, 30)),
        metrics_page(metric(3, "7")),
    )
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 2
    assert first.json()["items"][0]["root"]["trace_id"] == f"{3:032x}"
    assert json.loads(backend.requests[-1].url.params["query"])["filters"][0]["value"] == [f"{3:032x}"]
    # Live data shrinks between pages. An empty requested page needs no metric call.
    backend.answer(roots_page(root(service, 1, 10), root(service, 3, 30)))
    second = await service.client.get(
        f"{service.api}/traces/search", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 1 and second.json()["items"] == [] and second.json()["next_cursor"] is None
    assert len(backend.requests) == 3


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"order_by": "duration"}, [3, 2, 1, 4]),
        ({"order_by": "duration", "direction": "asc"}, [1, 2, 3, 4]),
        ({"order_by": "cost"}, [3, 2, 1, 4]),
        ({"order_by": "cost", "direction": "asc"}, [1, 2, 3, 4]),
        ({"min_duration_seconds": "20", "max_duration_seconds": "30", "order_by": "duration"}, [3, 2]),
        ({"min_cost_usd": "0", "max_cost_usd": "0"}, [1]),
        ({"min_cost_usd": "0.1", "max_cost_usd": "0.2", "max_duration_seconds": "25"}, [2]),
    ],
)
async def test_ranges_unknown_zero_and_direction(
    service: SimpleNamespace, backend: Backend, options: dict, expected: list[int]
) -> None:
    configure(service, backend)
    backend.answer(
        roots_page(*(root(service, i, i * 10 if i < 4 else None) for i in range(1, 5))),
        metrics_page(
            *(
                metric(i, cost)
                for i, cost in ((1, "0"), (2, "0.1"), (3, "0.2"))
                if options.get("order_by") != "duration" or i in expected
            )
        ),
    )
    result = await service.client.get(f"{service.api}/traces/search", params={**PARAMS, **options})
    assert result.status_code == 200, result.text
    assert [int(item["root"]["id"], 16) for item in result.json()["items"]] == expected


@pytest.mark.parametrize(
    "options",
    [
        {"min_cost_usd": "-1"},
        {"min_cost_usd": "NaN"},
        {"max_duration_seconds": "inf"},
        {"min_cost_usd": "2", "max_cost_usd": "1"},
        {"direction": "sideways"},
        {"started_before": START.isoformat()},
        {"started_after": START.replace(tzinfo=None).isoformat()},
    ],
)
async def test_invalid_ranges_never_read_backend(service: SimpleNamespace, backend: Backend, options: dict) -> None:
    configure(service, backend)
    result = await service.client.get(f"{service.api}/traces/search", params={**PARAMS, **options})
    assert result.status_code == 400, result.text
    assert backend.requests == []


@pytest.mark.parametrize("bound", ["roots", "summaries"])
async def test_collection_bound_fails_without_partial_rank(
    service: SimpleNamespace, backend: Backend, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    configure(service, backend)
    backend.answer(roots_page(root(service, 1), root(service, 2)), metrics_page(metric(1, "1")))
    if bound == "roots":
        monkeypatch.setattr(langfuse, "MAX_ROOTS", 1)
    else:
        monkeypatch.setattr(langfuse, "MAX_SUMMARY_BYTES", 1)
    result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
    assert result.status_code == 413 and "items" not in result.json()


@pytest.mark.parametrize("failure", ["backend", "cursor", "timeout"])
async def test_incomplete_collection_never_returns_rank(
    service: SimpleNamespace, backend: Backend, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    configure(service, backend)
    if failure == "timeout":
        monkeypatch.setattr(trace_search, "COLLECTION_SECONDS", 0)
    backend.answer(
        roots_page(root(service, 1), cursor="repeat"),
        httpx2.Response(503) if failure == "backend" else roots_page(root(service, 2), cursor="repeat"),
    )
    result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
    assert result.status_code == (408 if failure == "timeout" else 503)
    assert "items" not in result.json()


async def test_root_metadata_and_mcp_share_scoped_query(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend)
    backend.answer(roots_page(root(service, 1, stage="prod")), metrics_page(metric(1, "0.000000000000001")))
    async with client(service, await key(service)) as http:
        tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
        assert any(tool["name"] == "search_traces" for tool in tools)
        result = await call(
            http,
            service,
            "GET",
            "/traces/search",
            **PARAMS,
            attribute=["stage:prod"],
            min_cost_usd="0.000000000000001",
            order_by="cost",
        )
    assert result["status"] == 200, result
    assert Decimal(result["body"]["items"][0]["cost_usd"]) == Decimal("0.000000000000001")
    root_filters = json.loads(backend.requests[0].url.params["filter"])
    assert any(f.get("key") == "attributes.stage" for f in root_filters)
    cost_filters = json.loads(backend.requests[-1].url.params["query"])["filters"]
    assert not any(f.get("key") == "attributes.stage" for f in cost_filters)
    assert all(
        any(f.get("key") == f"attributes.{k}" and f["value"] == v for f in cost_filters)
        for k, v in scope(service).items()
    )


async def test_metric_limit_splits_batch_and_unknown_stays_unknown(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(langfuse, "METRIC_ROWS", 2)
    backend.answer(metrics_page(metric(1, "3", "input"), metric(1, "3")), metrics_page(metric(1, "3")), metrics_page())
    result = await backend.langfuse()._costs(
        [f"{1:032x}", f"{2:032x}"], {"workspace": "a"}, START, START + timedelta(hours=2)
    )
    assert result == {f"{1:032x}": Decimal(3)}
    batches = [json.loads(r.url.params["query"])["filters"][0]["value"] for r in backend.requests]
    assert list(map(len, batches)) == [2, 1, 1]


@pytest.mark.parametrize("rows", [[metric(9, "1")], [metric(1, "NaN")], [metric(1, "1"), metric(1, "2")]])
async def test_bad_aggregate_rows_are_not_trusted(backend: Backend, rows: list[dict]) -> None:
    backend.answer(metrics_page(*rows))
    with pytest.raises(ServiceError, match="unavailable"):
        await backend.langfuse()._costs([f"{1:032x}"], {}, START, START + timedelta(hours=1))


async def test_single_trace_category_overflow_fails(backend: Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langfuse, "METRIC_ROWS", 1)
    backend.answer(metrics_page(metric(1, "1")))
    with pytest.raises(ServiceError) as error:
        await backend.langfuse()._costs([f"{1:032x}"], {}, START, START + timedelta(hours=1))
    assert error.value.code == "payload_too_large"


async def test_backend_identity_binds_page_cursor(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend)
    backend.answer(roots_page(root(service, 1), root(service, 2)), metrics_page(metric(1, "0")))
    params = {**PARAMS, "limit": 1}
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    token = first.json()["next_cursor"]
    service.app.state.runtime = replace(
        service.runtime, traces=replace(backend.langfuse(), public_key="another-project")
    )
    result = await service.client.get(f"{service.api}/traces/search", params={**params, "cursor": token})
    assert result.status_code == 400
    assert len(backend.requests) == 2


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_logfire_pushes_aggregate_filter_sort_and_paging_without_cache(
    service: SimpleNamespace, backend: Backend, direction: str
) -> None:
    configure(service, backend, "logfire")

    def row(number: int, cost: str, remaining: int) -> dict:
        value = logfire_row(f"{number:016x}", {**scope(service), "stage": "o'reilly"})
        value.update(
            trace_id=f"{number:032x}", duration_seconds_text="10", trace_cost_text=cost, total=2, remaining=remaining
        )
        return value

    backend.answer(metrics_page(row(1, "0.000000000000001", 2)), metrics_page(row(2, "0.000000000000001", 1)))
    params = {
        **PARAMS,
        "order_by": "cost",
        "direction": direction,
        "limit": 1,
        "attribute": "stage:o'reilly",
        "min_cost_usd": "0.000000000000001",
        "max_duration_seconds": "30",
    }
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    assert "snapshot" not in first.json()
    assert Decimal(first.json()["items"][0]["cost_usd"]) == Decimal("0.000000000000001")
    second = await service.client.get(
        f"{service.api}/traces/search", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200 and second.json()["next_cursor"] is None, second.text
    assert second.json()["collected_at"] == first.json()["collected_at"]
    for i, request in enumerate(backend.requests):
        body = json.loads(request.content)
        sql = body["sql"]
        assert "SUM(TRY_CAST" in sql and "GROUP BY s.trace_id" in sql
        assert f"ORDER BY trace_cost {direction.upper()} NULLS LAST, trace_id ASC, span_id ASC LIMIT 1" in sql
        assert "OFFSET" not in sql
        if i:
            operator = ">" if direction == "asc" else "<"
            assert f"trace_cost {operator} 0.000000000000001" in sql
            assert f"trace_id > '{1:032x}'" in sql
            assert "trace_cost IS NULL" in sql
        assert "trace_cost >= 0.000000000000001" in sql
        assert "duration_seconds <= 30" in sql
        assert "CAST(r.duration AS DECIMAL(38, 9))" in sql
        assert "AS trace_cost_text" in sql  # distinct from the numeric ORDER BY expression
        assert "'o''reilly'" in sql and "'o''reilly'" not in sql.split("root_candidates AS")[0]
        assert all(
            f"attributes->>'{k}' = '{v}'" in sql.split("root_candidates AS")[0] for k, v in scope(service).items()
        )
        assert datetime.fromisoformat(body["max_timestamp"]) > START + timedelta(hours=2)
        assert body["limit"] == 1
    invalid = await service.client.get(
        f"{service.api}/traces/search",
        params={**params, "direction": "desc" if direction == "asc" else "asc", "cursor": first.json()["next_cursor"]},
    )
    assert invalid.status_code == 400


async def test_logfire_empty_result_keeps_total(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend, "logfire")
    backend.answer(metrics_page({"trace_id": None, "total": 0, "remaining": 0}))
    result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
    assert result.status_code == 200, result.text
    assert result.json()["items"] == [] and result.json()["total"] == 0


def test_logfire_history_limits_deduplication_to_selected_traces() -> None:
    """Execute the production selection CTEs; their SQL needs no Logfire dialect extensions."""
    query = SearchQuery(
        SpanQuery({"workspace": "ours", "stage": "prod"}, START, START + timedelta(hours=1), 25, roots=True),
        {"workspace": "ours"},
        "cost",
        "desc",
    )
    sql = _search_sql(query, START + timedelta(days=30), None)
    # SQLite executes the exact JSON selectors, windows and semi-join. Decimal aggregation and the
    # Logfire-only duration/level projection are covered separately, not emulated in this test.
    selection = sql.split(", costs AS (", 1)[0]
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE records (trace_id TEXT, span_id TEXT, parent_span_id TEXT, kind TEXT, "
            "start_timestamp TEXT, end_timestamp TEXT, attributes TEXT)"
        )

        def insert(
            trace: str,
            span: str,
            *,
            minute: int = 0,
            parent: str | None = None,
            workspace: str = "ours",
            stage: str | None = "prod",
            cost: str | None = None,
            seconds: int = 10,
        ) -> None:
            started = START + timedelta(minutes=minute)
            db.execute(
                "INSERT INTO records VALUES (?, ?, ?, 'span', ?, ?, ?)",
                (
                    trace,
                    span,
                    parent,
                    started.isoformat().replace("+00:00", "Z"),
                    (started + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
                    json.dumps({"workspace": workspace, "stage": stage, "gen_ai.usage.cost": cost}),
                ),
            )

        insert("selected", "root", cost="0.1")
        insert("selected", "root", cost="0.1")
        # Children after the selected hour still count and need not carry the root's metadata.
        insert("selected", "child", minute=120, parent="root", stage=None, cost="0.1")
        insert("selected", "child", minute=120, parent="root", stage=None, cost="0.2", seconds=20)
        insert("selected", "foreign", minute=120, parent="root", workspace="other", cost="99")
        insert("before", "root", minute=-1, cost="99")
        insert("at-end", "root", minute=60, cost="99")
        # Apply root metadata to the latest version, not a stale matching version.
        insert("changed", "root", cost="99")
        insert("changed", "root", stage="dev", cost="99", seconds=20)
        for index in range(1_000):
            insert(f"unrelated-{index}", "root", minute=1_440, cost="99")

        roots = db.execute(selection + " SELECT trace_id FROM roots").fetchall()
        assert roots == [("selected",)]
        # This is the wider-window deduplication input/output, before occurrence filtering. The
        # previous query included every unrelated trace here even though the result page was small.
        scoped = db.execute(selection + " SELECT trace_id, span_id FROM scoped").fetchall()
        assert sorted(scoped) == [("selected", "child")] * 2 + [("selected", "root")] * 2
        spans = db.execute(
            selection + " SELECT span_id, attributes->>'gen_ai.usage.cost' FROM spans ORDER BY span_id"
        ).fetchall()
        assert spans == [("child", "0.2"), ("root", "0.1")]


async def test_logfire_rechecks_roots_and_rejects_truncated_pages(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend, "logfire")
    for attributes, total in (({}, 1), (scope(service), 2)):
        row = logfire_row("1" * 16, attributes)
        row.update(total=total, remaining=total, duration_seconds_text="1", trace_cost_text="1")
        backend.answer(metrics_page(row))
        result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
        assert result.status_code == 503 and "items" not in result.json()


async def test_empty_query_recollects_without_metrics(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend)
    backend.answer(roots_page(), roots_page())
    for _ in range(2):
        result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 0 and result.json()["items"] == []
        assert result.json()["next_cursor"] is None and "snapshot" not in result.json()
    assert len(backend.requests) == 2


async def test_unsupported_metrics_never_falls_back_to_raw_spans(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend)
    backend.answer(roots_page(root(service, 1)), httpx2.Response(400, json={"message": "Unknown costByType"}))
    result = await service.client.get(f"{service.api}/traces/search", params=PARAMS)
    assert result.status_code == 503 and "items" not in result.json()
    assert [request.url.path for request in backend.requests] == [
        "/api/public/v2/observations",
        "/api/public/v2/metrics",
    ]


@pytest.mark.parametrize("order_by", ["duration", "cost"])
async def test_search_does_not_use_redis_or_reuse_prior_results(
    service: SimpleNamespace, backend: Backend, monkeypatch: pytest.MonkeyPatch, order_by: str
) -> None:
    configure(service, backend)

    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Trace search must not access Redis")

    monkeypatch.setattr(service.runtime.redis, "get", forbidden)
    monkeypatch.setattr(service.runtime.redis, "set", forbidden)
    for cost in ("1", "2"):
        backend.answer(roots_page(root(service, 1)), metrics_page(metric(1, cost)))
        result = await service.client.get(f"{service.api}/traces/search", params={**PARAMS, "order_by": order_by})
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["cost_usd"] == cost
    assert len(backend.requests) == 4


async def test_langfuse_batches_complete_candidates_and_keeps_ties_stable(
    service: SimpleNamespace, backend: Backend
) -> None:
    configure(service, backend)
    backend.answer(
        roots_page(*(root(service, i) for i in range(1, 101)), cursor="last"),
        roots_page(root(service, 101)),
        metrics_page(*(metric(i, "0.1") for i in range(1, 101))),
        metrics_page(metric(101, "10")),
    )
    result = await service.client.get(f"{service.api}/traces/search", params={**PARAMS, "order_by": "cost", "limit": 2})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["total"] == 101
    assert [int(item["root"]["trace_id"], 16) for item in body["items"]] == [101, 1]
    assert [len(json.loads(r.url.params["query"])["filters"][0]["value"]) for r in backend.requests[2:]] == [100, 1]


@pytest.mark.parametrize("order_by", ["started", "duration", "cost"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_keyset_keeps_unseen_rows_after_insertions_and_deleted_anchor(
    service: SimpleNamespace, backend: Backend, order_by: str, direction: str
) -> None:
    configure(service, backend)
    params = {**PARAMS, "order_by": order_by, "direction": direction, "limit": 2}

    def value(i: int) -> int:
        return i * 10 if direction == "asc" else 100 - i * 10

    def observation(i: int) -> dict:
        item = root(service, i, value(i))
        if order_by == "started":
            item["startTime"] = (START + timedelta(seconds=value(i))).isoformat()
            item["endTime"] = (START + timedelta(seconds=value(i) + 10)).isoformat()
        return item

    backend.answer(
        roots_page(*(observation(i) for i in (1, 2, 3, 4))),
        metrics_page(*(metric(i, str(value(i))) for i in ((1, 2, 3, 4) if order_by == "cost" else (1, 2)))),
    )
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    assert [int(i["root"]["trace_id"], 16) for i in first.json()["items"]] == [1, 2]
    # Insert a row ahead of page one, and remove both seen rows including the cursor's anchor.
    backend.answer(
        roots_page(*(observation(i) for i in (0, 3, 4))),
        metrics_page(*(metric(i, str(value(i))) for i in ((0, 3, 4) if order_by == "cost" else (3, 4)))),
    )
    second = await service.client.get(
        f"{service.api}/traces/search", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert [int(i["root"]["trace_id"], 16) for i in second.json()["items"]] == [3, 4]
    assert second.json()["total"] == 3 and second.json()["next_cursor"] is None


@pytest.mark.parametrize("order_by", ["duration", "cost"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_keyset_pages_through_equal_and_unknown_metrics(
    service: SimpleNamespace, backend: Backend, order_by: str, direction: str
) -> None:
    configure(service, backend)
    params = {**PARAMS, "order_by": order_by, "direction": direction, "limit": 1}
    cursor = None
    for expected in range(1, 5):
        backend.answer(
            roots_page(*(root(service, i, 10 if i < 3 else None) for i in (4, 2, 3, 1))),
            metrics_page(*(metric(i, "0.000000000000001") for i in (1, 2) if order_by == "cost" or i == expected)),
        )
        result = await service.client.get(
            f"{service.api}/traces/search", params={**params, **({"cursor": cursor} if cursor else {})}
        )
        assert result.status_code == 200, result.text
        assert [int(item["root"]["trace_id"], 16) for item in result.json()["items"]] == [expected]
        cursor = result.json()["next_cursor"]
        assert bool(cursor) == (expected != 4)


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_logfire_null_keyset_and_empty_remaining_page(
    service: SimpleNamespace, backend: Backend, direction: str
) -> None:
    configure(service, backend, "logfire")
    row = logfire_row(f"{1:016x}", scope(service))
    row.update(trace_id=f"{1:032x}", duration_seconds_text=None, trace_cost_text=None, total=2, remaining=2)
    backend.answer(metrics_page(row), metrics_page({"trace_id": None, "total": 1, "remaining": 0}))
    params = {**PARAMS, "order_by": "duration", "direction": direction, "limit": 1}
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    second = await service.client.get(
        f"{service.api}/traces/search", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 1 and second.json()["items"] == [] and second.json()["next_cursor"] is None
    sql = json.loads(backend.requests[-1].content)["sql"]
    assert f"duration_seconds IS NULL AND (trace_id > '{1:032x}'" in sql
    assert f"span_id > '{1:016x}'" in sql
    assert "OFFSET" not in sql


async def test_logfire_started_keyset_preserves_nanoseconds(service: SimpleNamespace, backend: Backend) -> None:
    configure(service, backend, "logfire")
    row = logfire_row(f"{1:016x}", scope(service))
    timestamp = "2026-09-23T10:00:00.123456789Z"
    row.update(
        trace_id=f"{1:032x}",
        start_timestamp=timestamp,
        duration_seconds_text="10",
        trace_cost_text="0",
        total=2,
        remaining=2,
    )
    backend.answer(metrics_page(row), metrics_page({"trace_id": None, "total": 1, "remaining": 0}))
    params = {**PARAMS, "order_by": "started", "limit": 1}
    first = await service.client.get(f"{service.api}/traces/search", params=params)
    assert first.status_code == 200, first.text
    second = await service.client.get(
        f"{service.api}/traces/search", params={**params, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    sql = json.loads(backend.requests[-1].content)["sql"]
    assert f"start_timestamp < '{timestamp}'" in sql and f"start_timestamp = '{timestamp}'" in sql


@pytest.mark.parametrize("backend_kind", ["langfuse", "logfire"])
@pytest.mark.parametrize(
    "position",
    [
        ["NaN", "1" * 32, "1" * 16],
        ["1 OR TRUE", "1" * 32, "1" * 16],
        ["1", "bad", "1" * 16],
        ["1", "1" * 32, "bad"],
        [1],
    ],
)
async def test_invalid_keyset_positions_never_read_backend(backend: Backend, backend_kind: str, position: list) -> None:
    query = SearchQuery(SpanQuery({}, START, START + timedelta(hours=1), 1), {}, "cost", "desc")
    owner = query.page_owner("owner")
    values = [START.isoformat(), *position] if backend_kind == "logfire" else position
    query = replace(query, cursor=cursors.encode(f"{backend_kind}-search-keyset", owner, *values))
    with pytest.raises(ServiceError) as error:
        await getattr(backend, backend_kind)().search_traces(query, "owner")
    assert error.value.code == "invalid_cursor"
    assert backend.requests == []
