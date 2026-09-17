// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Schema } from "../transport/client";
import { ThreadRunChoices } from "./thread-run-choices";
import { ModelPicker } from "./model-picker";

afterEach(cleanup);

const model: Schema<"ModelSummary"> = {
  model_id: "model-one",
  name: "Reasoning model",
  route: "custom:one",
  thinking: {
    status: "supported",
    default_summary: "High",
    options: [
      {
        value: null,
        label: "Model default",
        description: "Follow model settings",
      },
      { value: "low", label: "Low", description: "Lower effort" },
      { value: "high", label: "High", description: "Higher effort" },
    ],
  },
};
const other: Schema<"ModelSummary"> = {
  model_id: "model-two",
  name: "Other model",
  route: "custom:two",
  thinking: {
    status: "unknown",
    default_summary: "Custom settings",
    reason: "No reviewed controls",
    options: [
      {
        value: null,
        label: "Model default",
        description: "Follow model settings",
      },
    ],
  },
};
const catalog: Schema<"ThreadSelectorCatalog"> = {
  agents: [
    {
      agent_id: "agent",
      name: "Writer",
      model_id: model.model_id,
      source_path: "agents/writer.yaml",
    },
  ],
  models: [model, other],
  environments: [],
  harness_plugins: [],
  environment_run_extensions: [],
  mcp_servers: [],
};

function Choices() {
  const [modelId, setModelId] = useState<string>();
  const [thinking, setThinking] =
    useState<Schema<"SubmitRequest">["thinking"]>();
  return (
    <ThreadRunChoices
      catalog={catalog}
      agentId=""
      defaultAgentId="agent"
      modelId={modelId}
      thinking={thinking}
      disabled={false}
      onAgentChange={vi.fn()}
      onModelChange={setModelId}
      onThinkingChange={setThinking}
    />
  );
}

it("shows two compact choices and keeps default thinking distinct from explicit effort", async () => {
  const user = userEvent.setup();
  render(<Choices />);
  const trigger = screen.getByRole("button", { name: "Model and thinking" });
  expect(trigger.textContent).toBe("Reasoning model· High");
  expect(screen.getByRole("combobox", { name: "Agent" }).textContent).toBe(
    "Writer",
  );
  expect(screen.queryByRole("combobox", { name: "Thinking" })).toBeNull();
  trigger.focus();
  await user.keyboard("[Enter]");
  expect(screen.getByText("Following agent default")).toBeTruthy();
  expect(screen.getByText("Model default: High")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Using default" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: "Low" }));
  expect(trigger.textContent).toBe("Reasoning model· Low");
  // Clicking the selected segment must not turn an explicit selection into default.
  await user.click(screen.getByRole("button", { name: "Low" }));
  expect(
    screen.getByRole("button", { name: "Low" }).getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: "Use default" }));
  expect(trigger.textContent).toBe("Reasoning model· High");
  await user.keyboard("[Escape]");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(trigger);
});

it("searches models in the same popup, returns focus, and resets thinking on model change", async () => {
  const user = userEvent.setup();
  render(<Choices />);
  await user.click(screen.getByRole("button", { name: "Model and thinking" }));
  await user.click(screen.getByRole("button", { name: "Low" }));
  await user.click(screen.getByRole("button", { name: "Change model" }));
  const search = screen.getByRole("textbox", { name: "Search models" });
  expect(document.activeElement).toBe(search);
  await user.type(search, "custom:two");
  expect(screen.queryByRole("button", { name: "Reasoning model" })).toBeNull();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Other model" }));
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Change model" }),
  );
  expect(
    screen.getByRole("button", { name: "Model and thinking" }).textContent,
  ).toBe("Other model");
  expect(screen.getByText("No reviewed controls")).toBeTruthy();
  expect(screen.queryByRole("group", { name: "Thinking level" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Change model" }));
  expect(
    (screen.getByRole("textbox", { name: "Search models" }) as HTMLInputElement)
      .value,
  ).toBe("");
  await user.click(screen.getByRole("button", { name: /Agent default/ }));
  expect(screen.getByText("Following agent default")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Model and thinking" }).textContent,
  ).toBe("Reasoning model· High");
});

it("keeps unavailable selections visible and disambiguates duplicate model names", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const onThinkingChange = vi.fn();
  render(
    <ModelPicker
      models={[model, { ...other, name: model.name }]}
      defaultModelId={model.model_id}
      value="removed-model"
      thinking="max"
      onChange={onChange}
      onThinkingChange={onThinkingChange}
    />,
  );
  const trigger = screen.getByRole("button", { name: "Model and thinking" });
  expect(trigger.textContent).toContain("removed-model (unavailable)");
  expect(trigger.textContent).toContain("Unavailable thinking");
  expect(onChange).not.toHaveBeenCalled();
  expect(onThinkingChange).not.toHaveBeenCalled();
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: "Change model" }));
  expect(
    screen.getByRole("button", {
      name: /Reasoning model.*model-one.*custom:one/,
    }),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: /Reasoning model.*model-two.*custom:two/,
    }),
  ).toBeTruthy();
  await user.type(
    screen.getByRole("textbox", { name: "Search models" }),
    "missing",
  );
  expect(screen.getByText("No models found.")).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Back to model settings" }),
  );
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Change model" }),
  );
});

it("cannot change selections while active work disables the controls", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <ModelPicker
      models={[model]}
      defaultModelId={model.model_id}
      disabled
      onChange={onChange}
      onThinkingChange={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Model and thinking" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});
