"""Parallel seed work is bounded, ordered, and isolated by filesystem workspace."""

from pathlib import Path

import anyio
import pytest
from a13n_service.configuration.sources import load_settings

from dev.service.environment import LOCAL_CONFIG
from dev.service.seed_sessions import bulk_sessions


@pytest.mark.parametrize(("concurrency", "count"), [(2, 5), (60, 120), (60, 3)])
def test_bulk_sessions_overlap_only_in_distinct_workspaces(tmp_path, monkeypatch, concurrency, count):
    settings = load_settings(
        LOCAL_CONFIG, environ={}, overrides={"worker": {"concurrency": concurrency}, "filesystem": {"root": tmp_path}}
    )
    roots = {}
    active = set()
    peak = 0
    slots = min(count, concurrency)
    catalog = {"environment_provider_id": "provider", "agents": ["first", "second"], "assets": ["a", "b", "c", "d"]}

    class Client:
        def __init__(self):
            self.completed = []
            self.observed = False

        async def pages(self, path, *, params):
            assert path == "/workspace/runs"
            assert params["limit"] <= slots
            if not self.observed:
                self.observed = True
                yield {"items": [dict(item, sealed_at=None) for item in self.completed]}
                return
            # Force the observer to follow pages despite a larger requested page size.
            for item in reversed(self.completed):
                yield {"items": [item]}

    client = Client()

    async def check():
        barrier = anyio.Event()
        cancelled_or_finished = set()

        async def workspace(client, base, provider_id, root, name):
            identifier = root.name
            assert root.is_relative_to(tmp_path)
            root.mkdir(parents=True)
            roots[identifier] = root
            return {"environment_id": identifier, "template_id": "template-" + identifier, "root": str(root)}

        async def run(client, base, agent, prompt, *, environment_id, asset_id, wait_for_run):
            nonlocal peak
            index = int(prompt.split("brief ")[1].split(".")[0]) - 1
            assert environment_id not in active
            active.add(environment_id)
            peak = max(peak, len(active))
            if len(active) == slots:
                barrier.set()
            path = roots[environment_id] / "input.txt"
            path.write_text(str(index))
            try:
                await barrier.wait()
                await anyio.sleep(0)
                assert path.read_text() == str(index)
                assert agent == catalog["agents"][index % 2]
                assert bool(asset_id) == (index % 12 == 0)
                assert ("[fail]" in prompt) == (index % 20 == 19)
                result = {
                    "id": f"run_{index}",
                    "index": index,
                    "status": "failed" if "[fail]" in prompt else "completed",
                    "sealed_at": "now",
                }
                client.completed.append(result)
                return await wait_for_run(result["id"], result["status"])
            finally:
                active.remove(environment_id)
                cancelled_or_finished.add(index)

        monkeypatch.setattr("dev.service.seed_sessions.local_workspace", workspace)
        monkeypatch.setattr("dev.service.seed_sessions.run", run)
        with anyio.fail_after(5):
            results, environments = await bulk_sessions(client, "/workspace", catalog, settings, count)
        assert [result["index"] for result in results] == list(range(count))
        assert all(result["sealed_at"] for result in results)
        assert len(environments) == slots
        assert len({Path(value["root"]).resolve() for value in environments}) == slots
        assert peak == slots
        assert not active
        assert cancelled_or_finished == set(range(count))

    anyio.run(check)


def test_bulk_failure_cancels_other_slots_before_returning(tmp_path, monkeypatch):
    settings = load_settings(LOCAL_CONFIG, environ={}, overrides={"worker": {"concurrency": 2}})
    stopped = []

    async def check():
        other_started = anyio.Event()

        async def workspace(client, base, provider_id, root, name):
            return {"environment_id": root.name, "root": str(root)}

        async def run(client, base, agent, prompt, *, environment_id, asset_id, wait_for_run):
            if environment_id == "slot-01":
                await other_started.wait()
                raise RuntimeError("seed run failed")
            other_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                stopped.append(environment_id)

        monkeypatch.setattr("dev.service.seed_sessions.local_workspace", workspace)
        monkeypatch.setattr("dev.service.seed_sessions.run", run)
        with anyio.fail_after(5), pytest.raises(ExceptionGroup, match="TaskGroup"):
            await bulk_sessions(
                None,
                "/workspace",
                {"environment_provider_id": "provider", "agents": ["agent"], "assets": ["asset"]},
                settings,
                4,
            )
        assert stopped == ["slot-02"]

    anyio.run(check)
