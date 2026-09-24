import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { downloadBlob } from "../../shared/download";
import { ExportAgent } from "./export";
import { initialConfig } from "./configuration";
import { parseAgentFile } from "./transfer";

vi.mock("../../shared/download", () => ({ downloadBlob: vi.fn() }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));

it("copies and downloads the same complete saved configuration shown in raw preview", async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  const agent = {
    name: "Research",
    description: "Saved description",
    key: "research",
  } as Schema["Agent"];
  const config = {
    ...initialConfig(),
    model: { model_id: "mdl_0123456789abcdef0123" },
    instructions: "Preserve\nall instructions.",
  };
  render(<ExportAgent agent={agent} config={config} version={3} />);
  await user.click(screen.getByRole("button", { name: "Export agent" }));
  await user.click(screen.getByRole("button", { name: "Raw" }));
  const yaml = screen.getByLabelText("Agent YAML").textContent!;
  expect(parseAgentFile(yaml)).toEqual({
    schema_version: 1,
    name: agent.name,
    description: agent.description,
    config,
  });
  await user.click(screen.getByRole("button", { name: "Copy YAML" }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(yaml));
  await user.click(screen.getByRole("button", { name: "Download YAML" }));
  expect(downloadBlob).toHaveBeenCalledWith(expect.any(Blob), "research.yaml");
  const blob = vi.mocked(downloadBlob).mock.calls[0]![0];
  const text = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });
  expect(text).toBe(yaml);
  copy.mockRestore();
});
