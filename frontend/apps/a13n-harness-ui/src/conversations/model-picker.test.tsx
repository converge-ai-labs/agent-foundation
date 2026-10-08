// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { Schema } from "../transport/client";
import { ThreadRunChoices } from "./thread-run-choices";
import type { ModelControlValues } from "./model-controls";
import { ComposerSettings } from "./composer-settings";
import { ModelOptions } from "./model-picker";
import { ModelControlPanel } from "./model-controls";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const model: Schema<"ModelSummary"> = {
  model_id: "model-one",
  name: "Reasoning model",
  route: "custom:one",
  fast: { supported: true, state: "on" },
  thinking: {
    status: "supported",
    default_summary: "High",
    default_value: "high",
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
  fast: { supported: false, state: "default", reason: "No Fast support" },
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

function Choices({ defaultModelId }: { defaultModelId?: string } = {}) {
  const [modelId, setModelId] = useState<string>();
  const [controls, setControls] = useState<ModelControlValues>({});
  return (
    <ThreadRunChoices
      catalog={catalog}
      agentId=""
      defaultAgentId="agent"
      defaultModelId={defaultModelId}
      modelId={modelId}
      controls={controls}
      onControlsChange={setControls}
      disabled={false}
      onAgentChange={vi.fn()}
      onModelChange={setModelId}
    />
  );
}

const button = (name: string | RegExp) => screen.getByRole("button", { name });

it("shows only a read-only identity and one settings entry, with explicit default choices", async () => {
  const user = userEvent.setup();
  render(<Choices />);
  expect(
    screen.getByLabelText("Agent: Writer. Model: Reasoning model").textContent,
  ).toBe("Writer·Reasoning model");
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Model settings" })).toBeNull();
  const trigger = button("Agent & Model settings");
  trigger.focus();
  await user.keyboard("[Enter]");
  expect(screen.getByRole("dialog", { name: "Agent & Model" })).toBeTruthy();
  expect(screen.getByText("Model default: High")).toBeTruthy();
  expect(button("Use default thinking").getAttribute("aria-pressed")).toBe(
    "true",
  );
  expect(button("High").getAttribute("aria-pressed")).toBe("true");
  await user.click(button("High"));
  expect(button("Use default thinking").getAttribute("aria-pressed")).toBe(
    "false",
  );
  expect(button("High").getAttribute("aria-pressed")).toBe("true");
  await user.click(button(/Low/));
  expect(button("High").getAttribute("aria-pressed")).toBe("false");
  expect(button("Low").getAttribute("aria-pressed")).toBe("true");
  await user.click(button(/Low/));
  expect(button("Low").getAttribute("aria-pressed")).toBe("true");
  await user.click(button("Use default thinking"));
  expect(screen.getByText("Model default: High")).toBeTruthy();
  expect(button("High").getAttribute("aria-pressed")).toBe("true");
  await user.keyboard("[Escape]");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(trigger);
});

it("searches models in one panel and resets overrides on model changes", async () => {
  const user = userEvent.setup();
  render(<Choices />);
  await user.click(button("Agent & Model settings"));
  await user.click(button(/Low/));
  await user.click(button("Model"));
  await user.type(
    screen.getByRole("textbox", { name: "Search models" }),
    "custom:two",
  );
  expect(screen.queryByRole("button", { name: "Reasoning model" })).toBeNull();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  await user.click(button("Other model"));
  expect(screen.getByText("Model default: Custom settings")).toBeTruthy();
  expect(screen.getByText("No reviewed controls")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Low/ })).toBeNull();
  await user.click(button("Model"));
  expect(
    (screen.getByRole("textbox", { name: "Search models" }) as HTMLInputElement)
      .value,
  ).toBe("");
  await user.click(button(/Agent default/));
  expect(screen.getByText("Model default: High")).toBeTruthy();
});

it("follows the saved Thread default and restores it after an explicit Run choice", async () => {
  const user = userEvent.setup();
  render(<Choices defaultModelId="model-two" />);
  expect(
    screen.getByLabelText("Agent: Writer. Model: Other model"),
  ).toBeTruthy();
  await user.click(button("Agent & Model settings"));
  await user.click(button("Model"));
  expect(button(/Thread default/).getAttribute("aria-pressed")).toBe("true");
  await user.click(button("Reasoning model"));
  expect(button("Model").textContent).toBe("ModelReasoning model");
  await user.click(button("Model"));
  await user.click(button(/Thread default/));
  expect(button("Model").textContent).toBe("ModelOther model");
});

it("keeps unavailable selections visible and disambiguates duplicate model names", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <ModelOptions
      models={[model, { ...other, name: model.name }]}
      defaultModelId={model.model_id}
      value="removed-model"
      onChange={onChange}
    />,
  );
  expect(screen.getByText("removed-model (unavailable)")).toBeTruthy();
  expect(button(/Reasoning model.*model-one.*custom:one/)).toBeTruthy();
  expect(button(/Reasoning model.*model-two.*custom:two/)).toBeTruthy();
  await user.type(
    screen.getByRole("textbox", { name: "Search models" }),
    "missing",
  );
  expect(screen.getByText("No models found.")).toBeTruthy();
  expect(onChange).not.toHaveBeenCalled();
});

it("keeps Fast inside settings, distinguishes false from default, and resets on model changes", async () => {
  const user = userEvent.setup();
  render(<Choices />);
  expect(screen.queryByRole("button", { name: "Fast mode" })).toBeNull();
  await user.click(button("Agent & Model settings"));
  expect(screen.getByText("On")).toBeTruthy();
  await user.click(button("Fast mode"));
  expect(button("Fast mode").getAttribute("aria-pressed")).toBe("false");
  await user.keyboard("[Escape]");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(button("Agent & Model settings"));
  expect(button("Fast mode").getAttribute("aria-pressed")).toBe("false");
  await user.click(button("Use default Fast mode"));
  expect(screen.getByText("On")).toBeTruthy();
  await user.click(button("Fast mode"));
  await user.click(button("Model"));
  await user.click(button("Other model"));
  expect((button("Fast mode") as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("No Fast support")).toBeTruthy();
  await user.click(button("Model"));
  await user.click(button(/Agent default/));
  expect(screen.getByText("On")).toBeTruthy();
});

it("keeps unsupported overrides resettable instead of silently changing them", async () => {
  const user = userEvent.setup();
  const onControlsChange = vi.fn();
  render(
    <ComposerSettings>
      <ModelControlPanel
        model={other}
        controls={{ thinking: "max", fast: true, reasoning_mode: "pro" }}
        onControlsChange={onControlsChange}
      />
    </ComposerSettings>,
  );
  await user.click(button("Agent & Model settings"));
  expect(screen.getAllByText(/Unavailable selection/).length).toBeGreaterThan(
    0,
  );
  await user.click(button("Reasoning mode"));
  expect((button("Pro") as HTMLButtonElement).disabled).toBe(true);
  expect(onControlsChange).not.toHaveBeenCalled();
  await user.click(button(/Default/));
  expect(onControlsChange).toHaveBeenLastCalledWith({
    thinking: "max",
    fast: true,
    reasoning_mode: null,
  });
  await user.click(button("Use default thinking"));
  expect(onControlsChange).toHaveBeenLastCalledWith({
    thinking: null,
    fast: true,
    reasoning_mode: "pro",
  });
  await user.click(button("Use default Fast mode"));
  expect(onControlsChange).toHaveBeenLastCalledWith({
    thinking: "max",
    fast: null,
    reasoning_mode: "pro",
  });
});

it("cannot change selections while submission disables the controls", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <ThreadRunChoices
      catalog={catalog}
      agentId="agent"
      controls={{}}
      disabled
      onAgentChange={onChange}
      onModelChange={onChange}
      onControlsChange={onChange}
    />,
  );
  await user.click(button("Agent & Model settings"));
  for (const name of [
    "Agent",
    "Model",
    "Low",
    "Use default thinking",
    "Fast mode",
    "Use default Fast mode",
  ]) {
    expect((button(name) as HTMLButtonElement).disabled).toBe(true);
    await user.click(button(name));
  }
  expect(onChange).not.toHaveBeenCalled();
});

it("changes Agent inside settings and clears model controls for the new inherited Model", async () => {
  const user = userEvent.setup();
  function Agents() {
    const [agentId, setAgentId] = useState("agent");
    const [controls, setControls] = useState<ModelControlValues>({
      thinking: "low",
      fast: false,
    });
    return (
      <ThreadRunChoices
        catalog={{
          ...catalog,
          agents: [
            ...catalog.agents,
            {
              agent_id: "reviewer",
              name: "Reviewer",
              model_id: other.model_id,
              source_path: "agents/reviewer.yaml",
            },
          ],
        }}
        agentId={agentId}
        controls={controls}
        onControlsChange={setControls}
        disabled={false}
        onAgentChange={setAgentId}
        onModelChange={vi.fn()}
      />
    );
  }
  render(<Agents />);
  await user.click(button("Agent & Model settings"));
  await user.click(button("Agent"));
  await user.click(button(/^Reviewer/));
  expect(
    screen.getByLabelText("Agent: Reviewer. Model: Other model"),
  ).toBeTruthy();
  expect(screen.getByText("Model default: Custom settings")).toBeTruthy();
  await user.click(button("Agent"));
  await user.click(button(/^Writer/));
  expect(screen.getByText("Model default: High")).toBeTruthy();
  expect(screen.getByText("On")).toBeTruthy();
});
