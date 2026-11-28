from __future__ import annotations

import re
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import BranchRequest, NewThreadDefaults, RootOperationStatus
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration
from .test_host_git import commit, git, repository
from .test_sharing_webui import create_link, shared_server

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_tools_project_updates_fork_and_branch_independently(tmp_path, monkeypatch):
    captures = []
    url = "https://example.com/handbook"
    projected = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            assert {"link_thread_resource", "unlink_thread_resource"} <= {tool.name for tool in info.function_tools}
            captures.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            projected.append(
                [
                    block
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, UserPromptPart)
                    for block in re.findall(
                        r'<thread-links thread_id="[^"]+">.*?</thread-links>', str(part.content), re.DOTALL
                    )
                ]
            )
            if len(captures) == 1:
                yield {
                    0: DeltaToolCall(
                        name="link_thread_resource",
                        json_args='{"kind":"url","target":"HTTPS://EXAMPLE.COM:443/handbook","label":"Guide"}',
                        tool_call_id="link",
                    )
                }
            else:
                yield "Done."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    configuration = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(
        settings, configuration_path=configuration, host_mode="webui", instrumentation=None
    ) as app:
        source = await app.create_thread()
        thread_id = source.thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Save the handbook", input_surface="webui")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        empty = f'<thread-links thread_id="{thread_id}">\n\n</thread-links>'
        assert projected[0] == [empty]
        assert len(projected[1]) == 2
        assert f'<url url="{url}" label="Guide" />' in projected[1][-1]
        assert "?native=changes&native_path=" in captures[1]
        saved = await app._store.threads.links(thread_id)
        assert saved[0].url == url
        assert await app._thread_links.link(thread_id, "url", url, "Guide", allow_host_git=False) == saved[0]
        updated = await app._thread_links.link(thread_id, "url", url, "Updated label", allow_host_git=False)
        assert updated.created_at == saved[0].created_at
        assert updated.updated_at > saved[0].updated_at
        points = await app.branch_points(thread_id)
        branched = await app.branch_thread(
            thread_id,
            BranchRequest(expected_continuation_id=points.source, turn_key=next(iter(points.points)), side="after"),
        )
        forked = await app.fork_thread(thread_id)
        assert await app._store.threads.links(branched.thread_id) == (updated,)
        assert await app._store.threads.links(forked.thread_id) == (updated,)
        assert await app._thread_links.unlink(thread_id, "url", url)
        assert not await app._thread_links.unlink(thread_id, "url", url)
        assert await app._store.threads.links(thread_id) == ()
        assert await app._store.threads.links(branched.thread_id) == (updated,)
        assert await app._store.threads.links(forked.thread_id) == (updated,)
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Next turn", input_surface="webui")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert projected[-1] == [*projected[1], empty]
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Unchanged", input_surface="webui")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert projected[-1] == projected[-2]


async def test_worktree_status_canonicalization_and_mode_do_not_expand_roots(tmp_path, monkeypatch):
    captures = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            captures.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            yield "Done."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    configuration = _write_configuration(tmp_path)
    root = repository(tmp_path / "repo")
    (root / "nested").mkdir()
    (root / "nested" / "file").write_text("test\n")
    commit(root)
    linked = tmp_path / "feature"
    git(root, "worktree", "add", "-b", "feature", str(linked))
    async with open_harness_ui_app(
        _settings(tmp_path / "data"),
        configuration_path=configuration,
        host_mode="webui",
        share_computer=True,
        instrumentation=None,
    ) as app:
        source = await app.create_thread(
            defaults=NewThreadDefaults(local_roots=(str(root),), environment_profile_id="environment-native")
        )
        scope = NewThreadDefaults(local_roots=(str(linked),), environment_profile_id="environment-native")
        linked_chat = await app.create_thread(defaults=scope)
        implicit = (await app.thread_links(linked_chat.thread_id)).worktrees
        assert len(implicit) == 1 and implicit[0].implicit and implicit[0].branch == "feature"
        secondary_default = await app.create_thread(
            defaults=NewThreadDefaults(
                local_roots=(str(root), str(linked)),
                default_environment="workspace-2",
                environment_profile_id="environment-native",
            )
        )
        secondary_implicit = (await app.thread_links(secondary_default.thread_id)).worktrees
        assert (
            len(secondary_implicit) == 1
            and secondary_implicit[0].implicit
            and secondary_implicit[0].branch == "feature"
        )
        thread_files_default = await app.create_thread(
            defaults=NewThreadDefaults(
                local_roots=(str(linked),),
                default_environment="thread-files",
                environment_profile_id="environment-native",
            )
        )
        thread_files_view = await app.thread_links(thread_files_default.thread_id)
        assert thread_files_view.worktrees == () and not thread_files_view.worktree_mode_available
        explicit = await app._thread_links.link(
            linked_chat.thread_id, "worktree", str(linked), None, allow_host_git=True
        )
        merged = (await app.thread_links(linked_chat.thread_id)).worktrees
        assert len(merged) == 1 and not merged[0].implicit
        nested = await app._thread_links.link(
            source.thread_id, "worktree", str(linked / "nested"), None, allow_host_git=True
        )
        assert nested == await app._thread_links.link(
            source.thread_id, "worktree", str(linked), None, allow_host_git=True
        )
        assert Path(nested.path).samefile(linked)
        assert explicit.path == nested.path
        git(linked, "checkout", "--detach")
        view = await app.thread_links(source.thread_id)
        assert view.worktrees[0].state == "detached"
        git(root, "worktree", "remove", str(linked))
        view = await app.thread_links(source.thread_id)
        assert view.worktrees[0].state == "removed"
        receipt = await app.submit_thread(
            thread_id=source.thread_id, prompt="Make the change", mode="worktree", input_surface="webui"
        )
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        assert "Work in an isolated linked Git worktree" in captures[-1]
        assert "link_thread_resource" in captures[-1]
        assert (
            await app.get_thread(source.thread_id)
        ).thread.configuration.local_roots == source.configuration.local_roots
        not_git = tmp_path / "not-git"
        not_git.mkdir()
        plain = await app.create_thread(
            defaults=NewThreadDefaults(local_roots=(str(not_git),), environment_profile_id="environment-native")
        )
        with pytest.raises(ThreadError) as error:
            await app.submit_thread(
                thread_id=plain.thread_id, prompt="Make the change", mode="worktree", input_surface="webui"
            )
        assert error.value.code == "worktree_mode_unavailable"


async def test_guest_cannot_read_unlink_or_probe_owner_status(tmp_path, monkeypatch):
    from a13n_harness_ui.github_link_status import GitHubLinkStatus

    probes = []

    async def status(self, client, url):
        probes.append(url)
        return None

    monkeypatch.setattr(GitHubLinkStatus, "status", status)
    async with shared_server(tmp_path, share_computer=True) as (_, app, client):
        thread_id = (await app.create_thread()).thread_id
        url = "https://github.com/org/private/issues/23"
        body = {"kind": "url", "target": url}
        await app._thread_links.link(thread_id, "url", url, None, allow_host_git=False)
        owner_route = f"/api/threads/{thread_id}/links"
        prefix, guest = await create_link(client, "thread", thread_id, permission="prompt")
        for route in (owner_route, f"{prefix}/threads/{thread_id}/links"):
            assert (await client.get(route, headers=guest)).status_code in (401, 403, 404)
            assert (await client.request("DELETE", route, json=body, headers=guest)).status_code in (401, 403, 404, 405)
        share_thread = await client.get(f"{prefix}/threads/{thread_id}", headers=guest)
        assert share_thread.is_success and url not in share_thread.text
        assert probes == []
        owner = {"Authorization": "Bearer " + "owner-test-key"}
        viewed = await client.get(owner_route, headers=owner)
        assert viewed.status_code == 200 and viewed.json()["urls"][0]["url"] == url
        assert probes == [url]
        assert (await client.request("DELETE", owner_route, json=body, headers=owner)).status_code == 204
        assert await app._store.threads.links(thread_id) == ()
