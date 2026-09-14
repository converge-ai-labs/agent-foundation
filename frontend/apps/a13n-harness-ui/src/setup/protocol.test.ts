import { afterAll, beforeAll, expect, it } from "vitest";
import { startSetupApp } from "../../tests/app-fixture";
import type { Schema } from "../transport/client";

let app: Awaited<ReturnType<typeof startSetupApp>>;
beforeAll(async () => {
  app = await startSetupApp();
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
      model_characteristics: { context_window: options.context_window },
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
  const id = `thread-${crypto.randomUUID().replaceAll("-", "")}`;
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
}, 30000);
