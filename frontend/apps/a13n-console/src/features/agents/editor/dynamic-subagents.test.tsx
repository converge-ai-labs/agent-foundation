import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DynamicSubagentsSettings } from "./dynamic-subagents";
import { buildDraftConfig, useAgentDraft } from "./draft";

const parent = {
  key: "parent",
  name: "Parent model",
  provider_id: "provider",
  config: { model_name: "parent", model_api: "openai.chat_completions" },
};
const research = { ...parent, key: "research", name: "Research model" };
vi.mock("../choices", () => ({
  useAgentChoices: () => ({
    data: {
      models: [
        parent,
        research,
        {
          ...parent,
          key: "voice",
          name: "Voice model",
          config: { model_api: "test.voice_protocol" },
        },
      ],
      providers: [
        {
          id: "provider",
          enabled: true,
          type: "openai",
          name: "OpenAI",
          config: {},
        },
      ],
    },
  }),
}));
vi.mock("../../models/provider-definitions", () => ({
  useModelProviderDefinitions: () => ({
    data: {
      items: [
        {
          realtime_model_apis: ["test.voice_protocol"],
          settings_schemas: {
            "openai.chat_completions": {
              properties: {
                thinking: {
                  anyOf: [{ type: "boolean" }, { enum: ["low", "high"] }],
                },
              },
            },
          },
        },
      ],
    },
  }),
}));
vi.mock("../../models/model-icon", async (original) => ({
  ...(await original<object>()),
  ModelKeyLabel: ({ modelKey }: { modelKey: string }) => (
    <span>{modelKey}</span>
  ),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (text: string, values?: Record<string, string>) =>
      text.replace("{{model}}", values?.model ?? ""),
  }),
}));
afterEach(cleanup);
function Editor({ model = "parent" }: { model?: string }) {
  const draft = useAgentDraft({
    model,
    subagents: {
      mode: "inline",
      dynamic: {
        allowed_models: ["research"],
        default_model: "research",
        default_thinking: false,
      },
    },
  });
  const built = buildDraftConfig(draft, undefined, (text) => text);
  return (
    <>
      <DynamicSubagentsSettings draft={draft} readOnly={false} />
      <button onClick={() => draft.setSubagentMode("async")}>Use async</button>
      <output>
        {built.ok
          ? JSON.stringify(built.config.subagents)
          : built.error.message}
      </output>
    </>
  );
}
it("requires an explicit async choice, preserves defaults and resets a removed default model", async () => {
  const user = userEvent.setup();
  render(<Editor />);
  expect(screen.queryByRole("combobox", { name: "Default model" })).toBeNull();
  await user.click(screen.getByRole("switch", { name: "Dynamic subagents" }));
  expect(
    screen.getByRole("combobox", { name: "Thinking effort" }).textContent,
  ).toContain("Off");
  expect(screen.getByRole("status").textContent).toBe(
    "Dynamic subagents require Async mode.",
  );
  await user.click(screen.getByRole("button", { name: "Use async" }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toMatchObject({
    mode: "async",
    dynamic: {
      enabled: true,
      default_model: "research",
      default_thinking: false,
    },
  });
  await user.click(
    screen.getByRole("button", { name: "Remove allowed model research" }),
  );
  expect(JSON.parse(screen.getByRole("status").textContent!)).toMatchObject({
    dynamic: {
      allowed_models: [],
      default_model: null,
      default_thinking: false,
    },
  });
  expect(
    screen.getByRole("combobox", { name: "Default model" }).textContent,
  ).toContain("Parent model");
});

it("requires a non-realtime default when a voice parent spawns dynamic subagents", async () => {
  const user = userEvent.setup();
  render(<Editor model="voice" />);
  await user.click(screen.getByRole("switch", { name: "Dynamic subagents" }));
  await user.click(screen.getByRole("button", { name: "Use async" }));
  await user.click(
    screen.getByRole("button", { name: "Remove allowed model research" }),
  );
  expect(
    screen.getAllByText("Subagents require a non-realtime model.").length,
  ).toBeGreaterThan(0);
  const field = screen.getByRole("combobox", { name: "Default model" });
  expect(field.getAttribute("aria-invalid")).toBe("true");
});
