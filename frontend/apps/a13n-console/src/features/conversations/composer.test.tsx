import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Composer } from "./composer";

vi.mock("../../auth/context", () => ({ useClient: () => ({ http: {} }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function mount(submit: (input: unknown, key: string) => Promise<unknown>) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <Composer submit={submit} />
    </QueryClientProvider>,
  );
  return screen.getByRole("textbox", { name: "Message" });
}
it("preserves a failed draft and its command key until the retry succeeds", async () => {
  const submit = vi
    .fn()
    .mockRejectedValueOnce(new Error("Try again"))
    .mockResolvedValueOnce(undefined);
  const input = mount(submit);
  fireEvent.change(input, { target: { value: "Keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("alert");
  expect((input as HTMLTextAreaElement).value).toBe("Keep this draft");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  expect(submit).toHaveBeenCalledTimes(2);
  expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1]);
});
it("sends with Cmd/Ctrl+Enter while preserving ordinary Enter and IME input", async () => {
  const submit = vi.fn().mockResolvedValue(undefined);
  const input = mount(submit);
  fireEvent.change(input, { target: { value: "A message" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true, isComposing: true });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter", metaKey: true });
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0]![0].content).toEqual([
    { type: "text", text: "A message" },
  ]);
});
it("retains message and attachment drafts when closing the attachment dialog", async () => {
  const user = userEvent.setup(),
    submit = vi.fn().mockResolvedValue(undefined);
  const input = mount(submit);
  await user.type(input, "Read this file");
  await user.click(screen.getByRole("button", { name: "Attach content" }));
  await user.type(
    screen.getByRole("textbox", { name: "File URL" }),
    "https://example.com/report.pdf",
  );
  await user.click(screen.getByRole("button", { name: "Attach URL" }));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect((input as HTMLTextAreaElement).value).toBe("Read this file");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0]![0].content).toContainEqual({
    type: "binary",
    source: { type: "url", url: "https://example.com/report.pdf" },
  });
});
