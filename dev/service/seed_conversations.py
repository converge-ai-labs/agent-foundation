"""Conversations executed for real against the scripted model, whose prompt markers select its behavior.

A marker stays in effect for the rest of its thread, because the model reads every user message of the history
(see `dev/fixtures/model.py`). Each scenario is independent, so the seed runs them in parallel.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dev.service.api import Api, ApiError, Json
from dev.service.checkout import ADMIN_PASSWORD
from dev.service.seed_agents import Cast
from dev.service.seed_identity import MEMBERS

type Scenario = Callable[[], dict[str, str]]
# How example files reach the model (spec 05): natively to the media model, which declares every understanding
# capability, or placed into the thread's environment; every other example is short text, inlined.
NATIVE = ("Preview swatches.png", "One second of silence.wav", "Release checklist.pdf")
PLACED = ("Project notes.zip", "Unknown format.bin", "Long scroll document.txt")


@dataclass(frozen=True, slots=True)
class Talk:
    """Submits messages in the session's workspace and waits for their runs."""

    api: Api

    def start(
        self,
        agent: Json,
        text: str,
        *,
        assets: Sequence[Json] = (),
        mount: Json | None = None,
        expect: str = "completed",
    ) -> Json:
        """A new thread's first run once sealed; `mount` is the environment it mounts as `workspace`."""
        mounts = [] if mount is None else [{"name": "workspace", "environment_id": mount["id"]}]
        body = {**_message(agent, text, assets), "environments": mounts}
        return self.sealed(self.api.post("/api/v1/threads", body, idempotent=True), expect)

    def reply(self, run: Json, agent: Json, text: str, *, assets: Sequence[Json] = (), **fields: object) -> Json:
        """The next run of `run`'s thread once sealed."""
        return self.sealed(self.submit(run["thread_id"], {**_message(agent, text, assets), **fields}))

    def submit(self, thread_id: str, message: Json) -> Json:
        return self.api.post(f"/api/v1/threads/{thread_id}/inbox", message, idempotent=True)

    def resume(self, run: Json, answer: Json) -> Json:
        """Answer the run's only pending call; the successor run once sealed."""
        pending = run["pending"]
        assert len(pending["approvals"]) + len(pending["calls"]) == 1
        category = "approvals" if pending["approvals"] else "calls"
        results = {"approvals": {}, "calls": {}}
        results[category] = {pending[category][0]["tool_call_id"]: answer}
        successor = self.api.post(f"/api/v1/runs/{run['id']}/resume", results, idempotent=True)
        return self.expect(self.api.sealed_run(successor["id"]), "completed")

    def running(self, submitted: Json) -> Json:
        return self.api.until(f"/api/v1/runs/{submitted['run']['id']}", lambda run: run["status"] == "running")

    def sealed(self, submitted: Json, expect: str = "completed") -> Json:
        return self.expect(self.api.sealed_run(submitted["run"]["id"]), expect)

    @staticmethod
    def expect(run: Json, status: str) -> Json:
        if run["status"] != status:
            raise ApiError(f"Run {run['id']} ended {run['status']} ({run['failure']}), not {status}")
        return run


def _message(agent: Json, text: str, assets: Sequence[Json] = ()) -> Json:
    parts = [{"type": "text", "text": text}, *({"type": "asset", "asset_id": asset["id"]} for asset in assets)]
    return {"agent_id": agent["id"], "payload": {"content": parts}}


def scenarios(talk: Talk, cast: Cast, environment: Json, assets: dict[str, Json]) -> tuple[Scenario, ...]:
    """Each scenario returns the IDs the report names."""
    api = talk.api

    def conversation() -> dict[str, str]:
        first = talk.start(
            cast.writer,
            "Draft release notes for the fictional Orbit 2.4 release.",
            assets=[assets["Release brief.md"]],
            mount=environment,
        )
        second = talk.reply(first, cast.writer, "[long] Expand the notes with a table of changes.")
        third = talk.reply(second, cast.writer, "Shorter, please; keep only the highlights.")
        session = api.get(f"/api/v1/sessions/{first['session_id']}")
        api.patch(f"/api/v1/sessions/{session['id']}", session, {"labels": {"release": "2.4"}})
        fork = api.post(
            f"/api/v1/runs/{second['id']}/fork",
            _message(cast.writer, "Alternative: keep the long table and drop the summary."),
            idempotent=True,
        )
        forked = talk.sealed(fork)
        return {"conversation": first["thread_id"], "conversation_last_run": third["id"], "fork": forked["thread_id"]}

    def workspace() -> dict[str, str]:
        skill = talk.start(cast.writer, "[workspace] Draft the notes with the release-notes skill.", mount=environment)
        note = "/workspace/notes/review.md"
        published = talk.start(cast.writer, f"[publish] {note}\nPublish the review notes.", mount=environment)
        analyst = talk.start(cast.analyst, "[workspace] Check the review notes for accessibility.")
        return {"skill_and_files": skill["id"], "published_asset": published["id"], "own_environment": analyst["id"]}

    def attachments() -> dict[str, str]:
        """One message per way a file reaches the model."""
        native, placed = [assets[name] for name in NATIVE], [assets[name] for name in PLACED]
        inline = [asset for name, asset in assets.items() if name not in NATIVE + PLACED]
        first = talk.start(
            cast.viewer, "Check the colour swatches, the recording and the checklist.", assets=native, mount=environment
        )
        second = talk.reply(first, cast.assistant, "And these documents, please.", assets=inline)
        third = talk.reply(second, cast.assistant, "Unpack the archive and skim the long document.", assets=placed)
        return {"native_files": first["id"], "inline_files": second["id"], "placed_files": third["id"]}

    def steered() -> dict[str, str]:
        submitted = api.post(
            "/api/v1/threads",
            _message(cast.assistant, "[interruptible] [steer-proof] Which colour is the release banner?"),
            idempotent=True,
        )
        talk.running(submitted)
        talk.submit(submitted["thread"]["id"], _message(cast.assistant, "[steer-update] Use the new palette."))
        run = talk.sealed(submitted)
        if run["output"] != "Revised answer: amber.":
            raise ApiError(f"Run {run['id']} did not incorporate its steer")
        return {"steered": run["id"]}

    def interrupted() -> dict[str, str]:
        submitted = api.post(
            "/api/v1/threads", _message(cast.assistant, "[interruptible] Draft the long launch plan."), idempotent=True
        )
        run = talk.running(submitted)
        queued = _message(cast.assistant, "Next: turn the plan into a checklist.")
        talk.submit(run["thread_id"], {**queued, "delivery": "next_run"})
        api.post(f"/api/v1/runs/{run['id']}/interrupt")
        talk.expect(api.sealed_run(run["id"]), "cancelled")
        return {"interrupted": run["id"]}

    def waits() -> dict[str, str]:
        approval = talk.start(cast.lookup, "[mcp] Look up the review record.", expect="waiting")
        approved = talk.resume(
            talk.start(cast.lookup, "[mcp] Look up the review.", expect="waiting"), {"action": "approve"}
        )
        tool_error = talk.start(cast.lookup, "[mcp-fail] Look up the missing review record.")
        client = talk.start(cast.reviewer, "[client] Review the Orbit 2.4 notes before publishing.", expect="waiting")
        result = {"status": "returned", "value": {"decision": "approved", "reason": "Reads well"}}
        completed = talk.resume(talk.start(cast.reviewer, "[client] Review the notes.", expect="waiting"), result)
        question = talk.start(cast.reviewer, "[service-wait:question] Which scope?", expect="waiting")
        return {
            "approval_waiting": approval["id"],
            "approval_approved": approved["id"],
            "tool_error": tool_error["id"],
            "client_tool_waiting": client["id"],
            "client_tool_completed": completed["id"],
            "question_waiting": question["id"],
        }

    def outcomes() -> dict[str, str]:
        structured = talk.start(cast.structured, "[structured] Return the review as a typed report.")
        malformed = talk.start(cast.structured, "[structured-invalid] Return a malformed report.", expect="failed")
        failed = talk.start(cast.assistant, "[fail] Summarize the fictional incident report.", expect="failed")
        delegated = talk.start(cast.coordinator, "[delegate] Ask the reviewer to check the release notes.")
        return {
            "structured_output": structured["id"],
            "malformed_output": malformed["id"],
            "failed": failed["id"],
            "delegated": delegated["id"],
        }

    def member() -> dict[str, str]:
        with Api(api.base_url) as runner:
            runner.login(MEMBERS["runner"][0], ADMIN_PASSWORD)
            runner.workspace_id = api.workspace_id
            run = Talk(runner).start(cast.assistant, "Where do I find last week's release notes?")
        return {"member_conversation": run["id"]}

    return (conversation, workspace, attachments, steered, interrupted, waits, outcomes, member)
