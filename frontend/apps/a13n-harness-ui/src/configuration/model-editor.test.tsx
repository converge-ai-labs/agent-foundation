// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { ModelFields } from "./model-editor";

const mocks = vi.hoisted(() => ({
  prepare: vi.fn(),
  get: vi.fn(async (path: string) => ({
    data:
      path === "/api/models/choices"
        ? {
            connections: [
              {
                id: "openai-responses",
                label: "API",
                provider: "openai-responses",
                authentication: "api_key",
                models: [],
                default_model: "reviewed",
                supports_base_url: true,
              },
            ],
          }
        : path === "/api/auth/keys"
          ? [{ credential_ref: "key-saved" }]
          : { items: [], status: "unavailable" },
  })),
}));
vi.mock("../transport/context", () => ({
  useTransport: () => ({
    client: {
      GET: mocks.get,
      POST: (path: string, body: unknown) =>
        path === "/api/models/prepare"
          ? mocks.prepare(body)
          : Promise.resolve({
              data: {
                name: "Reviewed model",
                presets: [
                  {
                    value: "high",
                    label: "High reasoning",
                    settings: { thinking: "high", max_tokens: 4000 },
                  },
                  {
                    value: "default",
                    label: "Provider defaults",
                    settings: {},
                  },
                ],
                native_tools: [],
              },
            }),
    },
  }),
}));
vi.mock("../setup/accounts", () => ({ ProviderAccount: () => null }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const original = `# Model comment
schema_version: '1'
kind: model
id: model-edit
name: Existing
route: openai-responses:old-custom
authentication: {kind: api_key, credential_ref: key-saved}
settings:
  # Keep custom setting
  temperature: 0.3
  thinking: high
  max_tokens: 4000
model_characteristics:
  # Local budget
  context_window: 128000
  capabilities: []
`;
function mount(initial = original) {
  function Editor() {
    const [source, setSource] = useState(initial);
    return (
      <>
        <ModelFields source={source} onChange={setSource} />
        <textarea
          aria-label="Advanced source"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        />
      </>
    );
  }
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <Editor />
    </QueryClientProvider>,
  );
}
function source() {
  return (screen.getByLabelText("Advanced source") as HTMLTextAreaElement)
    .value;
}
it("preserves YAML comments, empty capabilities and the authored context alias", async () => {
  mount();
  const input = await screen.findByLabelText("Working context budget");
  expect((input as HTMLInputElement).value).toBe("128000");
  expect(source()).toBe(original);
  fireEvent.change(input, { target: { value: "64000" } });
  expect(parse(source()).model_characteristics).toEqual({
    context_window: 64000,
    capabilities: [],
  });
  expect(source()).toContain("# Local budget");
  expect(source()).toContain("# Keep custom setting");
  expect(parse(source()).settings).toEqual(parse(original).settings);
});
it("provider defaults remove preset-owned fields without removing custom native settings", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Settings preset" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Provider defaults" }),
  );
  expect(parse(source()).settings).toEqual({ temperature: 0.3 });
  expect(source()).toContain("# Keep custom setting");
});
it("an old prepare cannot overwrite advanced edits after route-triggered remount", async () => {
  let complete!: (value: unknown) => void;
  mocks.prepare.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Model ID"), {
    target: { value: "new-model" },
  });
  const button = await screen.findByRole("button", {
    name: "Apply connection & defaults",
  });
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(button);
  await waitFor(() => expect(mocks.prepare).toHaveBeenCalledTimes(1));
  const advanced = original
    .replace("old-custom", "advanced-new")
    .replace("temperature: 0.3", "temperature: 0.9");
  fireEvent.change(screen.getByLabelText("Advanced source"), {
    target: { value: advanced },
  });
  await act(async () =>
    complete({
      data: {
        route: "openai-responses:new-model",
        authentication: { kind: "api_key", credential_ref: "key-saved" },
        settings: {},
      },
    }),
  );
  expect(source()).toBe(advanced);
});
