import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "vitest";
import { createClient } from "./index.js";

const wire = JSON.parse(
  await readFile(
    new URL(
      "../../../../../proto/a13n-service/fixtures/wire.json",
      import.meta.url,
    ),
    "utf8",
  ),
);

test("shared wire fixtures preserve omission, null and response metadata", async () => {
  const sent = [];
  const client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "bearer", token: "test-token" },
    fetch: async (request) => {
      sent.push(await request.json());
      assert.equal(request.headers.get("If-Match"), '"v1"');
      return new Response("{}", {
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": "req_test",
          ETag: '"v2"',
        },
      });
    },
  });
  for (const body of wire.patch) {
    const result = await client.http.PATCH(
      "/api/v1/workspaces/{workspace}/agents/{agent}",
      {
        params: {
          path: { workspace: "ws_example", agent: "agent_example" },
          header: { "If-Match": '"v1"' },
        },
        body,
      },
    );
    assert.equal(result.response.headers.get("ETag"), '"v2"');
    assert.equal(result.response.headers.get("X-Request-ID"), "req_test");
  }
  assert.deepEqual(sent, wire.patch);
  client.close();
});
