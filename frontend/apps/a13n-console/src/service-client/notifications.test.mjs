import assert from "node:assert/strict";
import { test } from "vitest";
import { createClient } from "./index.js";

class Socket {
  sent = [];
  closed = false;
  send(text) {
    this.sent.push(JSON.parse(text));
  }
  close() {
    this.closed = true;
  }
  receive(value) {
    this.onmessage({ data: JSON.stringify(value) });
  }
}

test("notifications subscribe, acknowledge heartbeat and close with the client", async () => {
  const socket = new Socket();
  const states = [],
    received = [],
    errors = [];
  const client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
  });
  client.notifications({
    subscriptions: [
      {
        subscription_id: "sub_one",
        scope: "workspace",
        resource_id: "ws_one",
        topics: ["run.updated"],
      },
    ],
    onState: (state) => states.push(state),
    onNotification: (event) => received.push(event),
    onError: (error) => errors.push(error),
    socketFactory: (url, protocol, headers) => {
      assert.equal(url, "wss://service.example/api/v1/notifications");
      assert.equal(protocol, "a13n.service.notifications.v1");
      assert.deepEqual(headers, {});
      return socket;
    },
  });
  socket.onopen();
  assert.equal(socket.sent[0].type, "subscribe");
  socket.receive({ type: "subscribed", subscription_ids: ["sub_one"] });
  socket.receive({ type: "heartbeat", nonce: "proof" });
  assert.deepEqual(socket.sent[1], { type: "heartbeat_ack", nonce: "proof" });
  socket.receive({
    type: "notification",
    schema_version: "1",
    notification_id: "ntf_one",
    subscription_id: "sub_one",
    topic: "run.updated",
    workspace_id: "ws_one",
    resource_type: "run",
    resource_id: "run_one",
    resource_version: null,
    session_id: null,
    thread_id: null,
    run_id: "run_one",
    occurred_at: "2026-09-08T00:00:00Z",
  });
  assert.equal(received.length, 1);
  client.close();
  assert.equal(socket.closed, true);
  assert.deepEqual(states, ["connecting", "connected", "closed"]);
  assert.deepEqual(errors, []);
});

test("bearer notifications require a header-capable adapter and malformed frames stop delivery", () => {
  const options = {
    subscriptions: [],
    onState() {},
    onNotification() {},
    onError() {},
  };
  const client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "bearer", token: "private" },
  });
  assert.throws(() => client.notifications(options), /socketFactory/);
  client.close();
  const socket = new Socket();
  let error;
  const browser = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
  });
  browser.notifications({
    ...options,
    socketFactory: () => socket,
    onError(value) {
      error = value;
    },
  });
  socket.receive({ type: "notification", schema_version: "invalid" });
  assert.equal(error.name, "ProtocolError");
  assert.equal(socket.closed, true);
});
