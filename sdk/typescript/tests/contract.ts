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

export const searchSelection: Schema["ToolSelection"] = {
  config: { provider_id: "wprov_test" },
};
export const searchOverrides: Schema["AgentRunOverride-Input"][] = [
  {},
  { toolsets: null },
  { toolsets: { web: { tools: { search: searchSelection } } } },
];
export const searchableAgent: Schema["AgentConfig-Input"] = {
  ...agentConfig,
  toolsets: { web: { tools: { search: searchSelection } } },
};
export const searchProviderRequest: Schema["CreateWebProviderRequest"] = {
  type: "brave",
  name: "Research",
  credential: { api_key: "test-secret" },
};
// @ts-expect-error Credentials are not a readable resource field.
export type ReadableSearchCredential = Schema["WebProvider"]["credential"];
import type { Client } from "../src/client.js";

export async function scopedHttpContract(client: Client) {
  const http = await client.workspaceHttp();
  await http.GET("/agents");
  await http.GET("/web-providers");
  await http.POST("/web-providers/{provider_id}/test", {
    params: { path: { provider_id: "wprov_test" } },
  });
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

export const actor: Schema["ActorRef"] = {
  principal_id: "system",
  principal_type: "system",
};
export const environment: Schema["EnvironmentSelection"] = {
  template_id: "etpl_example",
  version: null,
};
export const patchStates: Schema["UpdateAgentRequest"][] = [
  {},
  { name: null },
  { name: "new" },
];
// @ts-expect-error Structured patch fields cannot degrade to arbitrary JSON.
export const invalidPatch: Schema["UpdateAgentRequest"] = { name: 42 };
// @ts-expect-error Message content is a typed string-or-multimodal union.
export const invalidMessage: Schema["UserMessage"] = { id: "m1", content: 42 };
