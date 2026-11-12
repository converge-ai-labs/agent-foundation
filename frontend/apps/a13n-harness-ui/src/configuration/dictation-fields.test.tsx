// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { DictationFields } from "./dictation-fields";

vi.mock("../transport/context", () => ({
  useSelectors: () => ({
    data: {
      models: [
        {
          model_id: "audio-model",
          name: "Audio model",
          media_capabilities: ["audio"],
        },
      ],
    },
  }),
  useTransport: () => ({
    client: {
      GET: async (path: string) => ({
        data:
          path === "/api/models/choices"
            ? {
                connections: [
                  {
                    id: "codex",
                    models: [
                      { value: "supported-model", label: "Supported model" },
                    ],
                  },
                ],
              }
            : {
                groups: [
                  { id: "codex-work", name: "Work group", provider: "codex" },
                ],
                accounts: [
                  {
                    account: {
                      id: "acct-work",
                      name: "Personal account",
                      provider: "codex",
                      enabled: true,
                    },
                    authentication: { usable: true },
                  },
                ],
              },
      }),
    },
  }),
}));
afterEach(cleanup);
function setup(initial = 'schema_version: "1"\n') {
  function Fields() {
    const [source, onChange] = useState(initial);
    return (
      <>
        <DictationFields source={source} onChange={onChange} />
        <output data-testid="source">{source}</output>
      </>
    );
  }
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Fields />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
const source = () => parse(screen.getByTestId("source").textContent!);
async function choose(
  user: ReturnType<typeof userEvent.setup>,
  field: string,
  option: string,
) {
  await user.click(screen.getByRole("combobox", { name: field }));
  await user.click(await screen.findByRole("option", { name: option }));
}
it("enables Codex explicitly with usable presets and collapsed advanced settings", async () => {
  const user = setup();
  expect(
    screen.queryByRole("combobox", { name: "Subscription source" }),
  ).toBeNull();
  await choose(user, "Voice input", "Codex subscription");
  expect(source().dictation).toEqual({ backend: "codex" });
  expect(
    screen.getByRole("combobox", { name: "Subscription source" }).textContent,
  ).toContain("Default Codex group");
  expect(
    screen.getByText("Advanced dictation settings").closest("details")!.open,
  ).toBe(false);
  fireEvent.click(screen.getByText("Advanced dictation settings"));
  expect(
    screen.getByRole("combobox", { name: "Correction model" }).textContent,
  ).toContain("Preset Luna model");
  await choose(user, "Correction model", "Supported model");
  expect(source().dictation.codex.model).toBe("supported-model");
  expect(source().dictation.model).toBeUndefined();
});
it("selects an account or group, preserving advanced presets across backend changes", async () => {
  const user = setup(
    'schema_version: "1"\ndictation:\n  backend: codex\n  codex:\n    model: supported-model\n',
  );
  await choose(user, "Subscription source", "Personal account");
  expect(source().dictation.codex).toEqual({
    account: "acct-work",
    model: "supported-model",
  });
  await choose(user, "Subscription source", "Work group");
  expect(source().dictation.codex).toEqual({
    group: "codex-work",
    model: "supported-model",
  });
  await choose(user, "Voice input", "Off");
  expect(source().dictation.backend).toBe("model");
  await choose(user, "Voice input", "Codex subscription");
  expect(source().dictation.codex).toEqual({
    group: "codex-work",
    model: "supported-model",
  });
});
it("keeps a missing account visible without silently choosing another source", async () => {
  setup(
    'schema_version: "1"\ndictation:\n  backend: codex\n  codex:\n    account: acct-missing\n',
  );
  expect(
    await screen.findByText("account:acct-missing (unavailable)"),
  ).toBeTruthy();
  expect(source().dictation.codex.account).toBe("acct-missing");
});
