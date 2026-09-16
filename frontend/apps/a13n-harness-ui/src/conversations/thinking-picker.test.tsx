// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Schema } from "../transport/client";
import { ThinkingPicker } from "./thinking-picker";
import { ThreadRunChoices } from "./thread-run-choices";

afterEach(cleanup);

const model: Schema<"ModelSummary"> = {
  model_id: "custom-model",
  name: "Custom",
  route: "custom:provider-independent",
  thinking: {
    status: "supported",
    default_summary: "Budget · 8,192 tokens",
    options: [
      { value: null, label: "Model default", description: "Configured budget" },
      { value: false, label: "No thinking", description: "Disable thinking" },
      { value: "low", label: "Quick", description: "Budget: 2,048 tokens" },
      {
        value: "high",
        label: "Deep",
        description: "Budget: 16,384 tokens",
        disabled_reason: "Increase the output limit first",
      },
    ],
  },
};

it("renders backend labels and preserves false separately from default", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const view = render(<ThinkingPicker model={model} onChange={onChange} />);
  screen.getByRole("combobox", { name: "Thinking" }).focus();
  await user.keyboard("[ArrowDown]");
  await user.click(screen.getByRole("option", { name: /No thinking/ }));
  expect(onChange).toHaveBeenLastCalledWith(false);
  view.unmount();
  render(<ThinkingPicker model={model} value={false} onChange={onChange} />);
  expect(
    screen.getByRole("combobox", { name: "Thinking" }).textContent,
  ).toContain("No thinking");
  screen.getByRole("combobox", { name: "Thinking" }).focus();
  await user.keyboard("[ArrowDown]");
  const blocked = screen.getByRole("option", { name: /Deep/ });
  expect(blocked.getAttribute("aria-disabled")).toBe("true");
  await user.click(blocked);
  expect(onChange).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("option", { name: /Default · Budget/ }));
  expect(onChange).toHaveBeenLastCalledWith(null);
});

it("keeps unknown and stale selections explicit without inventing options", async () => {
  const user = userEvent.setup();
  const unknown = {
    ...model,
    thinking: {
      status: "unknown" as const,
      default_summary: "Custom",
      reason: "No reviewed controls",
      options: [{ value: null, label: "Model default", description: "Custom" }],
    },
  };
  const onChange = vi.fn();
  render(<ThinkingPicker model={unknown} value="low" onChange={onChange} />);
  expect(
    screen.getByRole("combobox", { name: "Thinking" }).textContent,
  ).toContain("Unavailable selection");
  screen.getByRole("combobox", { name: "Thinking" }).focus();
  await user.keyboard("[ArrowDown]");
  expect(await screen.findByText("No reviewed controls")).toBeTruthy();
  expect(await screen.findAllByRole("option")).toHaveLength(2);
  expect(onChange).not.toHaveBeenCalled();
});

it("resets thinking when a refreshed catalog changes the inherited Model", () => {
  const onThinkingChange = vi.fn();
  const catalog: Schema<"ThreadSelectorCatalog"> = {
    agents: [
      {
        agent_id: "agent",
        name: "Agent",
        model_id: model.model_id,
        source_path: "agents/agent.yaml",
      },
    ],
    models: [model],
    environments: [],
    harness_plugins: [],
    environment_run_extensions: [],
    mcp_servers: [],
  };
  const props = {
    catalog,
    agentId: "agent",
    thinking: "low" as const,
    onThinkingChange,
    disabled: false,
    onAgentChange: vi.fn(),
    onModelChange: vi.fn(),
  };
  const view = render(<ThreadRunChoices {...props} />);
  expect(onThinkingChange).not.toHaveBeenCalled();
  view.rerender(
    <ThreadRunChoices
      {...props}
      catalog={{
        ...catalog,
        agents: [{ ...catalog.agents[0], model_id: "another-model" }],
      }}
    />,
  );
  expect(onThinkingChange).toHaveBeenCalledWith(null);
});
