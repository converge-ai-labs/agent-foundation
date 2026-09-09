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

export const searchSelection: Schema["SearchSelection"] = {
  provider_id: "sp_test",
};
export const searchOverrides: Schema["AgentRunOverride-Input"][] = [
  {},
  { search: null },
  { search: searchSelection },
];
export const searchableAgent: Schema["AgentConfig-Input"] = {
  ...agentConfig,
  search: searchSelection,
};
export const searchProviderRequest: Schema["CreateSearchProviderRequest"] = {
  type: "brave",
  name: "Research",
  credential: "test-secret",
};
// @ts-expect-error Credentials are not a readable resource field.
export type ReadableSearchCredential = Schema["SearchProvider"]["credential"];
