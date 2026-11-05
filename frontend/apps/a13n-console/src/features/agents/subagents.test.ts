import { expect, it } from "vitest";
import {
  subagentLimitsError,
  subagentNameError,
  suggestedSubagentName,
} from "./subagents";

it("suggests valid distinct calling names without changing existing names", () => {
  expect(suggestedSubagentName("Research agent", ["research_agent"])).toBe(
    "research_agent_2",
  );
  expect(suggestedSubagentName("数据助手", [])).toBe("subagent");
  expect(
    subagentNameError("research_agent", ["research_agent"], "research_agent"),
  ).toBeUndefined();
  expect(subagentNameError("research_agent", ["research_agent"])).toBeTruthy();
  expect(subagentNameError("invalid name", [])).toBeTruthy();
});
it("matches the Service's asynchronous request-only limit contract", () => {
  expect(
    subagentLimitsError("async", {
      request_limit: 10,
      input_tokens_limit: null,
      count_tokens_before_request: false,
    }),
  ).toBeUndefined();
  expect(subagentLimitsError("async", { request_limit: null })).toBeUndefined();
  expect(subagentLimitsError("async", { cost_limit: 0 })).toBeTruthy();
  expect(
    subagentLimitsError("async", { total_tokens_limit: 100 }),
  ).toBeTruthy();
  expect(
    subagentLimitsError("inline", {
      cost_limit: "0.1",
      total_tokens_limit: 100,
    }),
  ).toBeUndefined();
  expect(subagentLimitsError("inline", { request_limit: 1.5 })).toBeTruthy();
});
