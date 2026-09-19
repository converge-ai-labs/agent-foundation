import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MarkdownContent } from "../markdown";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("copies the complete highlighted code without the toolbar or Markdown fences", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  const code = 'const value = "<script>";\nconsole.log(value);\n';
  render(<MarkdownContent text={`\`\`\`javascript\n${code}\`\`\``} />);
  expect(screen.getByText("javascript")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Copy code" }));
  expect(write).toHaveBeenCalledExactlyOnceWith(code);
  expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
});
