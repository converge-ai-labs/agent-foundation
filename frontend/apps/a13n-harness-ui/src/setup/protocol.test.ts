import { afterAll, beforeAll, expect, it } from "vitest";
import { startApp } from "../../tests/app-fixture";
import type { Schema } from "../transport/client";

let app: Awaited<ReturnType<typeof startApp>>;
beforeAll(async () => {
  app = await startApp("--setup");
}, 40000);
afterAll(async () => {
  await app?.close();
}, 20000);
async function request(
  path: string,
  body?: unknown,
  method = body ? "POST" : "GET",
) {
  return fetch(`${app.origin}${path}`, {
    method,
    headers: {
      Authorization: "Bearer test-only-key",
      "Content-Type": "application/json",
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

it("takes an untouched real App from offline choices to saved configuration and one empty first Thread", async () => {
  const setup: Schema<"SetupStatus"> = await (
    await request("/api/setup")
  ).json();
  expect(setup.fresh).toBe(true);
  expect(setup.needed).toBe(true);
  expect(await (await request("/api/auth/logins")).json()).toBeNull();
  const provider = setup.choices!.api_providers.find(
    (p) => p.value === "openai-responses",
  )!;
  const model = provider.models[0]!;
  const options: Schema<"SetupModelOptions"> = await (
    await request("/api/setup/model-options", {
      provider: provider.value,
      model_id: model,
      base_url: provider.base_url,
    })
  ).json();
  expect(
    (
      await request(
        "/api/auth/keys",
        {
          credential_ref: "key-onboarding-fixture",
          key: "fake-local-test-key",
        },
        "PUT",
      )
    ).ok,
  ).toBe(true);
  const selection: Schema<"SetupSelection"> = {
    ...setup.choices!.defaults,
    providers: [],
    default_agent: "agent-api-key",
    api_key_model: {
      route: `${provider.value}:${model}`,
      authentication: {
        kind: "api_key",
        credential_ref: "key-onboarding-fixture",
      },
      settings: options.presets[0]!.settings,
      model_configuration: { base_url: provider.base_url },
      model_characteristics: { context_window_tokens: options.context_window },
    },
  };
  const preview: Schema<"SetupPreview"> = await (
    await request("/api/setup/preview", selection)
  ).json();
  expect(Object.keys(preview.files).length).toBeGreaterThan(2);
  expect(JSON.stringify(preview)).not.toContain("fake-local-test-key");
  expect((await request("/api/setup/apply", { selection })).ok).toBe(true);
  for (const [path, expected] of Object.entries(preview.files)) {
    const source = await (
      await request(`/api/configuration/sources/${path}`)
    ).json();
    expect(source.content).toBe(expected);
  }
  const saved = await (await request("/api/setup")).json();
  expect(saved.needed).toBe(false);
  expect(saved.fresh).toBe(false);
  expect(saved.draft_scope).toBe(setup.draft_scope);
  const id = `thread_${crypto.randomUUID().replaceAll("-", "")}`;
  const body = {
    thread_id: id,
    defaults: {
      agent_id: "agent-api-key",
      project_id: null,
      environment_profile_id: "environment-native",
    },
  };
  const created = await Promise.all([
    request("/api/threads", body),
    request("/api/threads", body),
  ]);
  expect(created.filter((response) => response.ok)).toHaveLength(1);
  const conflict = await created.find((response) => !response.ok)!.json();
  expect(conflict.error.code).toBe("thread_exists");
  const thread = await (await request(`/api/threads/${id}`)).json();
  expect(thread.thread.thread_id).toBe(id);
  expect(thread.thread.root_activity.state).toBe("inactive");
  expect((await (await request("/api/threads")).json()).total).toBe(1);

  const legacyId = `thread-${crypto.randomUUID().replaceAll("-", "")}`;
  expect(
    (await request("/api/threads", { ...body, thread_id: legacyId })).ok,
  ).toBe(true);
  const legacy = await (await request(`/api/threads/${legacyId}`)).json();
  expect(legacy.thread.thread_id).toBe(legacyId);
  for (const separator of ["_", "-"]) {
    for (const suffix of [
      "a".repeat(31),
      "a".repeat(33),
      "A".repeat(32),
      "g".repeat(32),
    ]) {
      const invalid = await request("/api/threads", {
        ...body,
        thread_id: `thread${separator}${suffix}`,
      });
      expect(invalid.status).toBe(400);
      expect((await invalid.json()).error.code).toBe("request_invalid");
    }
  }
}, 30000);
