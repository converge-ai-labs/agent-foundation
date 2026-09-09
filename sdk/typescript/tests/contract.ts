import type { components } from "../src/schema.js";

type Schema = components["schemas"];

// Wire defaults remain omittable, including nested request defaults.
export const ordinaryInput: Schema["AgentInput"] = { schema_version: "2" };
export const agentConfig: Schema["AgentConfig-Input"] = {
  model: { model_key: "example" },
  input_adapter: { adapter_key: "native" },
  protocol: { public_name: "Example" },
};
export const runSubmission: Schema["ThreadRunSubmissionRequest"] = {
  expected_thread_version: 1,
  input: ordinaryInput,
};

import type { Client } from "../src/client.js";

export async function scopedHttpContract(client: Client) {
  const http = await client.workspaceHttp();
  await http.GET("/agents");
  await http.GET("/agents/{agent}", {
    params: { path: { agent: "reviewer" } },
  });
  await http.PATCH("/agents/{agent}", {
    params: { path: { agent: "reviewer" }, header: { "If-Match": '"v1"' } },
    body: { key: "assistant" },
  });
  // @ts-expect-error Workspace is supplied by the credential, never by this caller.
  await http.GET("/agents", { params: { path: { workspace: "other" } } });
}
