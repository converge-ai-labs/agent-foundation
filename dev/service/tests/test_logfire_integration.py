"""Opt-in incremental execution against an already seeded, running local Service."""

import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import anyio
import httpx2
import pytest
from a13n_service.configuration.sources import load_settings
from a13n_service.trace_query import ProviderTraceQuery, ProviderTraceRead, TraceView
from a13n_service.trace_query.logfire import LogfireTraceQueryProvider

from dev.service.model import MODEL_URL
from dev.service.seed_client import Client
from dev.service.seed_identity import PASSWORD
from dev.service.seed_journeys import finish, run

_CONFIG = os.getenv("A13N_TEST_LOGFIRE_CONFIG")


async def pace_query(request):
    # Each Service observation page performs two backend reads. Keep this
    # opt-in test below the project's query budget; never retry mutations.
    if request.url.path == "/v2/query" or "/traces" in request.url.path:
        interval = float(os.getenv("A13N_TEST_LOGFIRE_QUERY_INTERVAL", "10"))
        await anyio.sleep(interval * (2 if request.url.path.endswith("/observations") else 1))


class LiveHTTPClient(httpx2.AsyncClient):
    async def send(self, request, **kwargs):
        for attempt in range(3):
            response = await super().send(request, **kwargs)
            retryable = (request.method == "GET" and "/traces" in request.url.path and response.status_code == 503) or (
                request.method == "POST" and request.url.path == "/v2/query" and response.status_code == 429
            )
            if not retryable or attempt == 2:
                return response
            await response.aclose()
            print(f"Transient trace read HTTP {response.status_code}; retrying after 60 seconds", flush=True)
            await anyio.sleep(60)
        raise AssertionError("Unreachable read retry state")


@asynccontextmanager
async def login(settings, workspace_id, *, email="admin@example.com"):
    async with LiveHTTPClient(
        base_url=f"http://{settings.service.host}:{settings.service.port}",
        headers={"Origin": settings.iam.public_origin, "X-A13N-Workspace-ID": workspace_id},
        timeout=30,
        trust_env=False,
        event_hooks={"request": [pace_query]},
    ) as http:
        client = Client(http)
        result = await client.request("POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        http.headers["X-A13N-CSRF-Token"] = result["csrf_token"]
        # Unlike browsers, the HTTP cookie jar does not exempt loopback HTTP
        # from Secure. Forward only the session obtained by ordinary login.
        http.headers["Cookie"] = f"a13n_session={http.cookies['a13n_session']}"
        try:
            yield client
        finally:
            await client.request("POST", "/api/v1/auth/logout", expected=204)


async def preflight(client, manifest):
    base = f"/api/v1/workspaces/{manifest['workspace_id']}"
    descriptor = await client.request("GET", base + "/trace-query")
    assert descriptor["provider"] == "logfire" and descriptor["enabled"]
    model = await client.request("GET", base + "/models/" + manifest["scenarios"]["resources"]["model_ready"])
    assert model["key"] == model["upstream_model"] == "local-scripted"
    provider = await client.request("GET", base + "/model-providers/" + model["provider_id"])
    assert provider["configuration"]["base_url"] == MODEL_URL
    connection = await client.request(
        "GET", "/api/v1/connections/" + manifest["scenarios"]["connectivity"]["mcp_ready"]
    )
    assert connection["source"]["endpoint_url"] == MODEL_URL.removesuffix("/v1") + "/mcp"
    agents = {
        "plain": manifest["agent_ids"][3],
        "tool": manifest["scenarios"]["connectivity"]["agent_mcp"],
        "client": manifest["scenarios"]["resources"]["agent_client_tool"],
    }
    for agent_id in agents.values():
        agent = await client.request("GET", base + "/agents/" + agent_id)
        revision = await client.request("GET", "/api/v1/agent-revisions/" + agent["default_revision_id"])
        assert revision["config"]["model"]["model_key"] == "local-scripted"
    return agents


async def execute(client, manifest, agents, output):
    """Reuse seed journeys without initializing identity, resources or stores."""
    base = f"/api/v1/workspaces/{manifest['workspace_id']}"
    marker = "logfire-live-" + uuid4().hex[:12]
    evidence = {"marker": marker, "started_at": datetime.now(UTC).isoformat(), "runs": {}, "attempts": {}}

    async def retain(name, value):
        evidence["runs"][name] = value
        evidence["attempts"][name] = await client.collection(f"/api/v1/runs/{value['id']}/attempts")
        # Preserve IDs before any later scenario or query can fail. Never save login responses.
        output.joinpath("runs.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return value

    for name, agent, prompt in (
        ("plain", "plain", "Review this fictional release."),
        ("tool", "tool", "[mcp] Look up the fictional checklist."),
        ("tool_error", "tool", "[mcp-fail] Exercise the fictional tool failure."),
        ("long", "plain", "[long] Return the detailed fictional report."),
    ):
        await retain(
            name,
            await run(
                client,
                base,
                agents[agent],
                f"{marker} {prompt}",
                expected="failed" if name == "tool_error" else "completed",
            ),
        )
    failed = await retain("failed", await run(client, base, agents["plain"], f"{marker} [fail] Fictional failure."))
    thread = await client.request("GET", f"/api/v1/threads/{failed['thread_id']}")
    receipt = await client.request(
        "POST",
        f"/api/v1/runs/{failed['id']}/retry",
        expected=202,
        json={"expected_thread_version": thread["version"]},
    )
    await retain("retry", await finish(client, receipt["run_id"], "failed"))
    waiting = await retain(
        "waiting",
        await run(client, base, agents["client"], f"{marker} [client] Review this release.", expected="waiting"),
    )
    pending = await client.collection(f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending) == 1 and pending[0]["kind"] == "client_tool"
    thread = await client.request("GET", f"/api/v1/threads/{waiting['thread_id']}")
    receipt = await client.request(
        "POST",
        f"/api/v1/runs/{waiting['id']}/feedback",
        expected=202,
        json={
            "expected_thread_version": thread["version"],
            "sealed_state_digest_sha256": waiting["sealed_state_digest_sha256"],
            "resolutions": [
                {
                    "call_id": pending[0]["call_id"],
                    "action": "complete",
                    "result": {"decision": "approved-for-local-demo", "reason": marker},
                }
            ],
        },
    )
    completed = await retain("feedback", await finish(client, receipt["run_id"]))
    assert "approved-for-local-demo" in completed["output_text"]
    return evidence


async def raw_query(http, settings, sql, start, end):
    query = settings.observability.query
    response = await http.post(
        query.logfire_base_url.rstrip("/") + "/v2/query",
        headers={"Authorization": f"Bearer {query.logfire_read_token.get_secret_value()}"},
        json={"sql": sql, "min_timestamp": start.isoformat(), "max_timestamp": end.isoformat(), "limit": 1000},
    )
    # Do not include response headers or a credential-bearing request in assertion output.
    assert response.status_code == 200, f"Logfire query returned HTTP {response.status_code}"
    return response.json()["data"]


def sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


async def verify_readback(client, settings, manifest, evidence, output):
    query = settings.observability.query
    start = datetime.fromisoformat(evidence["started_at"]) - timedelta(seconds=1)
    end = datetime.now(UTC) + timedelta(minutes=5)
    expected = {a["id"] for items in evidence["attempts"].values() for a in items}
    base = f"/api/v1/workspaces/{manifest['workspace_id']}"
    workspace = await client.request("GET", base)
    org = workspace["organization_id"]
    ids = ", ".join(sql_literal(value) for value in sorted(expected))
    root_sql = (
        "SELECT * FROM records WHERE kind = 'span' AND span_name = 'a13n.service.run_attempt' "
        "AND parent_span_id IS NULL "
        f"AND attributes->>'a13n.organization.id' = {sql_literal(org)} "
        f"AND attributes->>'a13n.workspace.id' = {sql_literal(manifest['workspace_id'])} "
        f"AND attributes->>'a13n.run_attempt.id' IN ({ids}) ORDER BY start_timestamp DESC LIMIT 100"
    )
    async with LiveHTTPClient(timeout=30, trust_env=False, event_hooks={"request": [pace_query]}) as http:
        with anyio.fail_after(120):
            while True:
                roots = await raw_query(http, settings, root_sql, start, end)
                if {r["attributes"]["a13n.run_attempt.id"] for r in roots} == expected:
                    break
                await anyio.sleep(1)
        output.joinpath("raw-roots.json").write_text(json.dumps(roots, indent=2) + "\n")
        assert len(roots) == len(expected)
        trace_ids = ", ".join(sql_literal(root["trace_id"]) for root in roots)
        raw_rows = await raw_query(
            http,
            settings,
            f"SELECT * FROM records WHERE kind = 'span' AND trace_id IN ({trace_ids}) ORDER BY start_timestamp DESC, trace_id DESC, span_id DESC LIMIT 1000",
            start,
            end,
        )
        assert len(raw_rows) < 1000, "Raw evidence exceeded its explicit bound"
        output.joinpath("raw-all.json").write_text(json.dumps(raw_rows, indent=2) + "\n")
        provider = LogfireTraceQueryProvider(
            http,
            base_url=query.logfire_base_url,
            read_token=query.logfire_read_token.get_secret_value(),
            history_from=query.logfire_history_from,
        )
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "query": evidence["marker"],
            "search_in": "input",
            "view": "full",
            "limit": 2,
        }
        traces = await client.collection(base + "/traces", params=params)
        assert {t["correlation"]["run_attempt_id"] for t in traces} == expected
        assert len(traces) == len(expected)
        raw_traces = {}
        public_traces = {}
        for trace in traces:
            trace_id = trace["id"]
            attempt_id = trace["correlation"]["run_attempt_id"]
            assert trace["correlation"]["organization_id"] == org
            assert trace["correlation"]["workspace_id"] == manifest["workspace_id"]
            read = ProviderTraceRead(
                org, manifest["workspace_id"], trace_id, query.logfire_history_from, end, TraceView.full
            )
            direct = await provider.get_trace(read)
            assert direct is not None and direct.model_dump(mode="json") == trace
            detail_path = base + "/traces/" + trace_id
            assert await client.request("GET", detail_path) == trace
            raw = [row for row in raw_rows if row["trace_id"] == trace_id]
            raw_traces[trace_id] = raw
            # Exercise multi-page observation reads on one trace, without
            # multiplying every scenario's requests against a metered backend.
            limit = 5 if attempt_id == evidence["attempts"]["plain"][0]["id"] else 100
            full = await client.collection(detail_path + "/observations", params={"view": "full", "limit": limit})
            compact = await client.collection(detail_path + "/observations", params={"view": "compact", "limit": limit})
            assert len({o["id"] for o in full}) == len(full) == len(raw)
            assert [o["id"] for o in full] == [r["span_id"] for r in raw] == [o["id"] for o in compact]
            by_id = {o["id"]: o for o in full}
            assert by_id[trace["root"]["id"]] == trace["root"]
            assert all(o["parent_id"] is None or o["parent_id"] in by_id for o in full)
            for item, projected, row in zip(full, compact, raw, strict=True):
                assert item["parent_id"] == row["parent_span_id"]
                assert item["cost_usd"] is projected["cost_usd"] is None
                assert item["usage"] == projected["usage"]
                assert item["model"] == projected["model"]
                for field in (
                    "input",
                    "output",
                    "attributes",
                    "resource_attributes",
                    "scope",
                    "status_message",
                    "events",
                ):
                    assert projected[field] is None
                attrs = row["attributes"]
                usage = {k: v for k, v in attrs.items() if k.startswith("gen_ai.usage.") and k != "gen_ai.usage.cost"}
                assert item["usage"] == (usage or None)
            attempt = await client.request("GET", "/api/v1/run-attempts/" + attempt_id)
            assert attempt["run_id"] == trace["correlation"]["run_id"]
            public_traces[attempt_id] = {"trace": trace, "full": full, "compact": compact}
            output.joinpath("raw-observations.json").write_text(json.dumps(raw_traces, indent=2) + "\n")
            output.joinpath("public.json").write_text(json.dumps(public_traces, indent=2) + "\n")

        # Provider pagination independently retains the same roots and Attempt IDs.
        provider_query = ProviderTraceQuery(org, manifest["workspace_id"], start, end, 2, TraceView.full)
        provider_ids = []
        for _ in range(100):
            page = await provider.list_traces(provider_query)
            provider_ids.extend(t.id for t in page.items)
            if page.next_cursor is None:
                break
            provider_query = replace(provider_query, cursor=page.next_cursor)
        else:
            pytest.fail("Provider root pagination did not terminate")
        assert {t["id"] for t in traces} <= set(provider_ids)
        assert len(provider_ids) == len(set(provider_ids))
    verify_scenarios(evidence, public_traces, raw_traces)
    await verify_boundaries(client, settings, manifest, traces[0], params)
    output.joinpath("verified.json").write_text(
        json.dumps(
            {
                "marker": evidence["marker"],
                "attempts": len(expected),
                "traces": len(traces),
                "observations": sum(len(value["full"]) for value in public_traces.values()),
                "verified_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n"
    )
    return public_traces


def verify_scenarios(evidence, public, raw):
    for name, attempts in evidence["attempts"].items():
        retained = evidence["runs"][name]
        for attempt in attempts:
            result = public[attempt["id"]]
            trace, observations = result["trace"], result["full"]
            root = trace["root"]
            correlation = trace["correlation"]
            assert correlation["run_attempt_id"] == attempt["id"]
            assert correlation["run_id"] == retained["id"]
            for field in ("session_id", "thread_id", "agent_id"):
                assert correlation[field] == retained[field]
            assert root["input"] is not None
            assert evidence["marker"] in json.dumps(root["input"]["value"])
            if retained["status"] == "completed":
                assert root["output"]["value"] == retained["output_text"]
            else:
                assert root["output"] is None
            assert root["status"] == ("error" if retained["status"] == "failed" else "unset")
            assert {o["type"] for o in observations} >= {"span", "agent", "generation"}
            assert any(o["name"] == "a13n.service.reconstruct" for o in observations)
            for item, row in zip(observations, raw[trace["id"]], strict=True):
                assert item["status"] == row["otel_status_code"].lower()
                assert item["attributes"] == row["attributes"]
                assert item["resource_attributes"] == row["otel_resource_attributes"]
                assert item["scope"]["name"] == row["otel_scope_name"]
                assert len(item["events"]) == len(row["otel_events"])
                for event, native in zip(item["events"], row["otel_events"], strict=True):
                    assert event["name"] == native["event_name"]
                    assert datetime.fromisoformat(event["occurred_at"]) == datetime.fromisoformat(
                        native["event_timestamp"]
                    )
                    assert event["attributes"] == native["attributes"]
                for direction in ("input", "output"):
                    attrs = row["attributes"]
                    for key in (f"{direction}.value", f"a13n.{direction}", f"gen_ai.{direction}.messages"):
                        if key in attrs:
                            assert item[direction] is not None
                            assert item[direction]["value"] == attrs[key]
                            break
                if item["type"] == "generation" and retained["status"] == "completed":
                    assert item["model"] == {"requested": "local-scripted", "response": "local-scripted"}
                    assert item["usage"]["gen_ai.usage.input_tokens"] == 20
                    assert isinstance(item["input"]["value"], list)
                    assert isinstance(item["output"]["value"], list)
            if name == "tool":
                assert any(o["type"] == "tool" for o in observations)
                assert "LOCAL-REVIEW-42" in root["output"]["value"]
            if name == "tool_error":
                assert any(o["type"] == "tool" and o["status"] == "error" for o in observations)
            if name in {"tool_error", "failed", "retry"}:
                assert any(o["events"] for o in observations)
            if name == "long":
                assert len(root["output"]["value"]) > 1000
    assert evidence["runs"]["failed"]["thread_id"] == evidence["runs"]["retry"]["thread_id"]
    assert evidence["runs"]["waiting"]["thread_id"] == evidence["runs"]["feedback"]["thread_id"]
    assert evidence["attempts"]["waiting"][0]["id"] != evidence["attempts"]["feedback"][0]["id"]


async def verify_boundaries(client, settings, manifest, trace, params):
    base = f"/api/v1/workspaces/{manifest['workspace_id']}/traces"
    first = await client.request("GET", base, params=params)
    assert first["next_cursor"] is not None
    await client.request(
        "GET", base, expected=400, params={**params, "cursor": first["next_cursor"], "view": "compact"}
    )
    history = settings.observability.query.logfire_history_from
    await client.request(
        "GET",
        base,
        expected=400,
        params={"from": (history - timedelta(seconds=1)).isoformat(), "to": history.isoformat()},
    )
    other = f"/api/v1/workspaces/{manifest['empty_workspace_id']}/traces"
    with client.scope(manifest["empty_workspace_id"]):
        for suffix in ("", "/observations"):
            await client.request("GET", other + "/" + trace["id"] + suffix, expected=404)
    async with httpx2.AsyncClient(base_url=client.http.base_url, trust_env=False) as anonymous:
        for suffix in ("", "/" + trace["id"], "/" + trace["id"] + "/observations"):
            assert (await anonymous.get(base + suffix)).status_code == 401


@pytest.mark.parametrize(
    "method,path,status,retried",
    [
        ("GET", "/api/v1/workspaces/ws/traces/trace/observations", 503, True),
        ("POST", "/v2/query", 429, True),
        ("POST", "/api/v1/workspaces/ws/runs", 503, False),
        ("GET", "/api/v1/workspaces/ws/traces/trace", 404, False),
    ],
)
def test_live_read_retry_never_replays_execution(monkeypatch, method, path, status, retried):
    requests = []
    sleeps = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(status)

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(anyio, "sleep", sleep)

    async def check():
        async with LiveHTTPClient(transport=httpx2.MockTransport(handle), base_url="http://localhost") as http:
            assert (await http.request(method, path)).status_code == status

    anyio.run(check)
    assert len(requests) == (3 if retried else 1)
    assert sleeps == ([60, 60] if retried else [])


@pytest.mark.skipif(
    not _CONFIG, reason="A13N_TEST_LOGFIRE_CONFIG explicitly enables retained local Runs and remote OTLP"
)
def test_seeded_service_logfire_round_trip(tmp_path):
    assert _CONFIG is not None
    settings = load_settings(Path(_CONFIG))
    from dev.service.resolution import resolve_environment

    environment = resolve_environment(Path(_CONFIG))
    assert environment is not None
    environment.validate()
    assert settings.service.host == "127.0.0.1"
    assert settings.observability.tracing and settings.observability.trace_content == "standard"
    assert settings.observability.query.provider == "logfire"
    manifest = json.loads((environment.state / "seed.json").read_text())
    output = Path(os.getenv("A13N_TEST_LOGFIRE_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    assert not output.joinpath("runs.json").exists(), (
        "Use a new output directory to preserve earlier execution evidence"
    )

    async def check():
        async with login(settings, manifest["workspace_id"]) as client:
            agents = await preflight(client, manifest)
            replay = os.getenv("A13N_TEST_LOGFIRE_RUNS")
            if replay:
                evidence = json.loads(Path(replay).read_text())
                assert (
                    evidence["runs"].keys()
                    == evidence["attempts"].keys()
                    == {"plain", "tool", "tool_error", "long", "failed", "retry", "waiting", "feedback"}
                )
                output.joinpath("runs.json").write_text(json.dumps(evidence, indent=2) + "\n")
            else:
                evidence = await execute(client, manifest, agents, output)
            await verify_readback(client, settings, manifest, evidence, output)

    anyio.run(check)
