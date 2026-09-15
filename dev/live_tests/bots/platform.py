"""Independently authored Slack and encrypted Feishu event fixtures."""

import base64
import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .peer import APP_SECRET, ENCRYPT_KEY, SIGNING_SECRET, TOKEN, VERIFICATION_TOKEN


def account_config(platform):
    if platform == "slack":
        return {
            "provider_key": "slack",
            "provider_config_version": "slack_http_v1",
            "provider_config": {"api_app_id": "A_LIVE", "team_id": "T_LIVE", "bot_user_id": "U_BOT"},
            "credentials": {"bot_token": TOKEN, "signing_secret": SIGNING_SECRET},
        }
    assert platform == "lark"
    return {
        "provider_key": "lark",
        "provider_config_version": "lark_http_v1",
        "provider_config": {
            "brand": "feishu",
            "open_api_origin": "https://open.feishu.cn",
            "app_id": "cli_live",
            "tenant_key": "tenant-live",
            "bot_open_id": "ou_bot",
        },
        "credentials": {"app_secret": APP_SECRET, "encrypt_key": ENCRYPT_KEY, "verification_token": VERIFICATION_TOKEN},
    }


def signed_event(platform, marker, case, *, conversation=None, stale_reference=None):
    now = str(int(time.time()))
    text = marker + "\nLIVE_TEST " + json.dumps(case)
    if stale_reference:
        text = "STALE_MEMORY_REFERENCE " + stale_reference + "\n" + text
    if platform == "slack":
        message_id = f"{time.time():.6f}"
        body = json.dumps(
            {
                "type": "event_callback",
                "api_app_id": "A_LIVE",
                "team_id": "T_LIVE",
                "event_id": "Ev" + uuid4().hex,
                "authorizations": [
                    {"team_id": "T_LIVE", "user_id": "U_BOT", "is_bot": True, "is_enterprise_install": False}
                ],
                "event": {
                    "type": "app_mention",
                    "channel": conversation or "C_LIVE",
                    "user": "U_HUMAN",
                    "ts": message_id,
                    "text": "<@U_BOT> " + text,
                },
            }
        ).encode()
        signature = hmac.new(SIGNING_SECRET.encode(), b"v0:" + now.encode() + b":" + body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": now,
            "X-Slack-Signature": "v0=" + signature,
        }
        return body, headers, "X-Slack-Signature", message_id
    assert platform == "lark"
    message_id = "om_" + uuid4().hex
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": uuid4().hex,
            "event_type": "im.message.receive_v1",
            "create_time": now,
            "token": VERIFICATION_TOKEN,
            "app_id": "cli_live",
            "tenant_key": "tenant-live",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_human"}, "sender_type": "user", "tenant_key": "tenant-live"},
            "message": {
                "message_id": message_id,
                "create_time": now + "000",
                "chat_id": conversation or "oc_live",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 " + text}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "Live bot"}],
            },
        },
    }
    padder = PKCS7(128).padder()
    plaintext = json.dumps(payload).encode()
    padded = padder.update(plaintext) + padder.finalize()
    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(hashlib.sha256(ENCRYPT_KEY.encode()).digest()), modes.CBC(iv)).encryptor()
    body = json.dumps(
        {"encrypt": base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()}
    ).encode()
    nonce = uuid4().hex
    signature = hashlib.sha256((now + nonce + ENCRYPT_KEY).encode() + body).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": now,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }
    return body, headers, "X-Lark-Signature", message_id


def replies(events):
    return [item for item in events if item["method"] == "chat.postMessage" or item["method"].endswith("/reply")]


def assert_reply(platform, reply, message_id, expected, *, conversation=None):
    if platform == "slack":
        assert reply["body"] == {"channel": conversation or "C_LIVE", "thread_ts": message_id, "text": expected}
    else:
        assert reply["method"] == f"im/v1/messages/{message_id}/reply"
        body = reply["body"]
        assert set(body) == {"content", "msg_type", "reply_in_thread", "uuid"}
        assert body["msg_type"] == "text" and body["reply_in_thread"] is True and body["uuid"]
        assert json.loads(body["content"]) == {"text": expected}
