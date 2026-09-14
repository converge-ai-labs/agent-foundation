import assert from "node:assert/strict";
import test from "node:test";
import { createClient, ApiError } from "../dist/index.js";
const baseUrl = "https://service.example";
const path = { workspace: "ws_test", provider_id: "sp_test" };

test("Web Provider create, rotation, and tests never automatically replay", async () => {
  let calls = 0;
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "service-token" },
    fetch: async (request) => {
      calls++;
      assert.equal(request.redirect, "error");
      return new Response(
        JSON.stringify({
          error: { code: "unavailable", message: "Try later" },
        }),
        { status: 503 },
      );
    },
  });
  await assert.rejects(
    client.http.POST("/api/v1/workspaces/{workspace}/web-providers", {
      params: { path },
      body: { type: "brave", name: "Research", credential: "test-secret" },
    }),
    ApiError,
  );
  await assert.rejects(
    client.http.PATCH(
      "/api/v1/workspaces/{workspace}/web-providers/{provider_id}",
      {
        params: { path, header: { "If-Match": '\"v1\"' } },
        body: { credential: "test-secret" },
      },
    ),
    ApiError,
  );
  await assert.rejects(
    client.http.POST(
      "/api/v1/workspaces/{workspace}/web-providers/{provider_id}/test",
      { params: { path }, body: {} },
    ),
    ApiError,
  );
  assert.equal(calls, 3);
  client.close();
});

test("search override omits, disables, or replaces without default filling", async () => {
  const bodies = [];
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "token" },
    fetch: async (request) => {
      bodies.push(await request.json());
      return new Response("{}", {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  for (const config_override of [
    {},
    { search: null },
    { search: { provider_id: "sp_test" } },
  ]) {
    await client.http.POST("/api/v1/threads/{thread_id}/runs", {
      params: { path: { thread_id: "thread_test" } },
      body: {
        expected_thread_version: 1,
        input: { schema_version: "2" },
        config_override,
      },
    });
  }
  assert.deepEqual(
    bodies.map((body) => body.config_override),
    [{}, { search: null }, { search: { provider_id: "sp_test" } }],
  );
  client.close();
});

test("scoped account responses retain ETags and do not copy secret inputs", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "token" },
    fetch: async (request) => {
      requests.push(request);
      return new Response(
        JSON.stringify({ id: "sp_test", credential_configured: true }),
        { headers: { ETag: '\"v1\"', "Content-Type": "application/json" } },
      );
    },
  });
  const result = await client.http.POST(
    "/api/v1/organizations/{organization}/web-providers",
    {
      params: { path: { organization: "org_test" } },
      body: { type: "exa", name: "Research", credential: "test-secret" },
    },
  );
  assert.equal(result.response.headers.get("ETag"), '\"v1\"');
  assert.ok(!JSON.stringify(result.data).includes("test-secret"));
  assert.equal((await requests[0].json()).credential, "test-secret");
  client.close();
});

test("workspace-bound search uses credential context and shares shutdown", async () => {
  const urls = [];
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "token" },
    fetch: async (request) => {
      urls.push(request.url);
      return Response.json(
        request.url.endsWith("/auth/context")
          ? { workspace_id: "ws_test", workspace_key: "renamed" }
          : { items: [], next_cursor: null },
      );
    },
  });
  const http = await client.workspaceHttp();
  await http.GET("/web-providers");
  await http.GET("/web-providers/{provider_id}/references", {
    params: { path: { provider_id: "sp_test" } },
  });
  assert.deepEqual(urls, [
    `${baseUrl}/api/v1/auth/context`,
    `${baseUrl}/api/v1/workspaces/ws_test/web-providers`,
    `${baseUrl}/api/v1/workspaces/ws_test/web-providers/sp_test/references`,
  ]);
  client.close();
  await assert.rejects(http.GET("/web-providers"));
});
