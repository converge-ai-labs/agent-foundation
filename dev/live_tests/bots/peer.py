"""Independent Slack HTTP responses and retained actual outbound request evidence."""

import json
from uuid import uuid4

import anyio
from fastapi import APIRouter, HTTPException, Request

TOKEN = "fictional-bot-live-token"
SIGNING_SECRET = "fictional-bot-live-signing"
APP_SECRET = "fictional-feishu-app-secret"
ENCRYPT_KEY = "fictional-feishu-encrypt-key"
VERIFICATION_TOKEN = "fictional-feishu-verification"


def router(root):
    routes = APIRouter()

    @routes.api_route("/api/{method}", methods=["GET", "POST"])
    async def slack(method: str, request: Request):
        if request.headers.get("authorization") != "Bearer " + TOKEN:
            raise HTTPException(401, "Unexpected test credential")
        body = dict(request.query_params) if request.method == "GET" else await request.json()
        observation = {"method": method, "body": body}
        async with await anyio.Path(root / "bot-requests.jsonl").open("a") as output:
            await output.write(json.dumps(observation) + "\n")
        if method == "auth.test":
            return {"ok": True, "team_id": "T_LIVE", "team": "Live workspace", "user_id": "U_BOT", "bot_id": "B_LIVE"}
        if method == "bots.info":
            return {
                "ok": True,
                "bot": {"id": "B_LIVE", "user_id": "U_BOT", "app_id": "A_LIVE", "name": "Live bot", "deleted": False},
            }
        if method == "conversations.info":
            channel = body["channel"]
            if channel not in {"C_LIVE", "C_OTHER"}:
                raise HTTPException(404, "Unknown fixture channel")
            return {
                "ok": True,
                "channel": {
                    "id": channel,
                    "name": channel,
                    "is_member": not (root / (channel + "-removed")).exists(),
                    "is_archived": False,
                    "is_private": True,
                    "is_im": False,
                    "is_mpim": False,
                    "is_ext_shared": False,
                },
            }
        if method == "chat.postMessage":
            return {
                "ok": True,
                "channel": body["channel"],
                "ts": "1700000000." + str(int(uuid4().hex[:8], 16)),
                "message": {"thread_ts": body.get("thread_ts")},
            }
        raise HTTPException(404, "Unimplemented fixture method")

    @routes.api_route("/open-apis/{path:path}", methods=["GET", "POST"])
    async def feishu(path: str, request: Request):
        body = dict(request.query_params) if request.method == "GET" else await request.json()
        if path == "auth/v3/tenant_access_token/internal":
            if body != {"app_id": "cli_live", "app_secret": APP_SECRET}:
                raise HTTPException(401, "Unexpected test application credential")
            return {"code": 0, "tenant_access_token": TOKEN, "expire": 7200}
        if request.headers.get("authorization") != "Bearer " + TOKEN:
            raise HTTPException(401, "Unexpected test tenant token")
        async with await anyio.Path(root / "bot-requests.jsonl").open("a") as output:
            await output.write(json.dumps({"method": path, "body": body}) + "\n")
        if path == "bot/v3/info":
            return {"code": 0, "bot": {"open_id": "ou_bot", "app_name": "Live bot", "activate_status": 2}}
        if path == "tenant/v2/tenant/query":
            return {"code": 0, "data": {"tenant": {"tenant_key": "tenant-live", "name": "Live tenant"}}}
        if path in {"im/v1/chats/oc_live", "im/v1/chats/oc_other"}:
            return {
                "code": 0,
                "data": {
                    "name": "Live group",
                    "tenant_key": "tenant-live",
                    "chat_mode": "group",
                    "chat_type": "private",
                    "external": False,
                },
            }
        if path in {"im/v1/chats/oc_live/members/is_in_chat", "im/v1/chats/oc_other/members/is_in_chat"}:
            return {"code": 0, "data": {"is_in_chat": not (root / (path.split("/")[3] + "-removed")).exists()}}
        if path.startswith("im/v1/messages/om_") and path.endswith("/reply"):
            return {
                "code": 0,
                "data": {
                    "message_id": "om_reply_" + uuid4().hex,
                    "root_id": path.split("/")[3],
                    "thread_id": "omt_live",
                },
            }
        raise HTTPException(404, "Unimplemented fixture operation")

    return routes
