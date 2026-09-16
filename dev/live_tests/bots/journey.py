"""Native HTTP delivery and evidence shared by Bot lifecycle journeys."""

import json

import httpx2

from .peer import APP_SECRET, ENCRYPT_KEY, SIGNING_SECRET, TOKEN, VERIFICATION_TOKEN
from .platform import assert_reply, replies, signed_event


def events(root):
    path = root / "workspace" / "bot-requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def converse(
    lab, journey, platform, account, target, conversation, expected, *, stale_reference=None, case=None, recover=None
):
    live = lab.client
    base = f"/api/v1/application-accounts/{account['id']}"
    target = await live.request("GET", base + f"/targets/{target['id']}")
    before = len(replies(events(lab.root)))
    test = await journey.post(
        base + "/bot/tests",
        {"expected_version": account["version"], "target_id": target["id"], "target_version": target["version"]},
        expected=200,
    )
    assert len(replies(events(lab.root))) == before
    case = case or await live.case("bot_memory_revoked" if stale_reference else "bot_memory")
    body, headers, signature_header, message_id = signed_event(
        platform, test["id"], case, conversation=conversation, stale_reference=stale_reference
    )
    url = lab.config["connectivity_url"] + f"/connectivity/v1/accounts/{account['id']}/events"
    async with httpx2.AsyncClient(timeout=15, trust_env=False) as ingress:
        rejected = await ingress.post(url, content=body, headers={**headers, signature_header: "invalid"})
        assert rejected.status_code == 401
        untouched = await live.request("GET", base + f"/bot/tests/{test['id']}")
        assert not untouched["latest"]["event_received_at"] and not untouched["latest"]["run_id"]
        for _ in range(2):
            accepted = await ingress.post(url, content=body, headers=headers)
            assert accepted.status_code == 200, accepted.text

    async def observation():
        return (await live.request("GET", base + f"/bot/tests/{test['id']}"))["latest"]

    admitted = await live.wait(observation, lambda value: bool(value["run_id"]), "Bot Run acceptance")
    live.runs.append(admitted["run_id"])
    failed = None
    if recover:
        failed = await live.finish(admitted["run_id"], "failed")
        assert not (await observation())["reply"] and len(replies(events(lab.root))) == before
        await recover(failed)
        thread = await live.thread(failed["thread_id"])
        retry = await journey.post(
            f"/api/v1/runs/{failed['id']}/retry", {"expected_thread_version": thread["version"]}, expected=202
        )
        live.track(retry)
        result = await live.finish(retry["run_id"])
        assert result["retry_of_run_id"] == failed["id"]
        assert (await live.run(failed["id"]))["status"] == "failed"
    else:
        result = await live.finish(admitted["run_id"])
    if failed:
        observed = await observation()
        # A changed Account/target deliberately invalidates the setup test. The
        # retried Run's canonical delivery receipt remains independently readable.
        assert observed["stale"] and observed["reply"] is None
        page = await live.request("GET", base + "/bot/replies", params={"run_id": result["id"]})
        assert page["next_cursor"] is None
        receipts = page["items"]
        assert len(receipts) == 1 and receipts[0]["status"] == "succeeded"
        assert receipts[0]["run_id"] == result["id"]
    else:
        observed = await live.wait(observation, lambda value: bool(value["reply"]), "Native Bot reply")
        assert observed["reply"]["status"] == "succeeded" and observed["reply"]["run_id"] == result["id"]
    assert observed["event_received_at"] and observed["accepted_at"] and not observed["rejection_code"]
    sent = replies(events(lab.root))
    assert len(sent) == before + 1
    assert_reply(platform, sent[-1], message_id, test["id"] + "\n" + expected, conversation=conversation)
    requests = [
        json.loads(line)["body"]
        for line in (lab.root / "workspace" / case["case_id"] / "observations.jsonl").read_text().splitlines()
    ]
    expected_requests = 4 if case["scenario"] == "bot_memory_parent" else 3
    if failed:
        initial = 0
        while initial < len(requests) and not any(
            message.get("role") == "tool" for message in requests[initial]["messages"]
        ):
            initial += 1
        assert initial >= 2
        expected_requests += initial - 1
    assert len(requests) == expected_requests
    if case["scenario"] == "bot_memory_parent":
        plan = json.loads((lab.root / "workspace" / case["case_id"] / "plan.json").read_text())
        if plan["mode"] == "async":

            async def children():
                threads = await live.collection(f"/api/v1/sessions/{result['session_id']}/threads")
                return [t for t in threads if t["origin_run_id"] == result["id"]]

            threads = await live.wait(children, bool, "Actual child Thread")
            assert len(threads) == 1
            child_id = threads[0]["current_run_id"]
            live.runs.append(child_id)
            child = await live.finish(child_id)
            assert child["output_text"] == expected
        child_requests = journey.observations(plan["child"])
        assert len(child_requests) == 2
        assert expected not in json.dumps(child_requests[0])
        assert expected in json.dumps(child_requests[1])
        from .model import memory_index

        assert "memory://" in memory_index(child_requests[0]["body"])
        if plan["mode"] == "inline":
            assert expected in json.dumps(requests[1])
    for credential in (TOKEN, SIGNING_SECRET, APP_SECRET, ENCRYPT_KEY, VERIFICATION_TOKEN):
        assert credential not in json.dumps(requests)
    attempts = await lab.attempts(result["id"])
    assert len(attempts) == 1 and attempts[0]["harness_run_id"]
    return requests
