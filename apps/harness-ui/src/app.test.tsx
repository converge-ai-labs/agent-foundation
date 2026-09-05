// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Conversation } from "./app";
import { emptyDraft, updateDrafts, type Drafts } from "./drafts";
import type { Model } from "./client";
const mocks = vi.hoisted(() => ({ navigate: vi.fn(), post: vi.fn() }));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  createRouter: () => ({ navigate: mocks.navigate }),
}));
vi.mock("./client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./client")>()),
  api: {
    GET: async (path: string) => ({
      data: path === "/api/selectors" ? { environments: [] } : [],
    }),
    POST: mocks.post,
  },
  result: (value: { data: unknown }) => value.data,
  stream: () => new Promise(() => {}),
}));
HTMLElement.prototype.scrollTo = vi.fn();
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const setup: Model<"SetupStatus"> = {
  needed: false,
  configuration_path: "/config",
  suggested_project_path: "/project",
  generation: "generation",
  providers: [],
  default_project: "project-local",
  default_agent: "agent-codex",
  environment_profile: "environment-native",
  projects: { "project-local": "Local" },
  agents: { "agent-codex": "Codex" },
};
function mount() {
  let state: Drafts = {};
  function Fixture() {
    const [drafts, setDrafts] = useState<Drafts>({
      new: { ...emptyDraft, text: "Prompt A" },
    });
    state = drafts;
    return (
      <Conversation
        id={null}
        setup={setup}
        draftState={drafts.new}
        dispatch={(action) =>
          setDrafts((current) => updateDrafts(current, action))
        }
      />
    );
  }
  const view = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Fixture />
    </QueryClientProvider>,
  );
  return { ...view, state: () => state };
}
it("keeps text edited during admission and navigates only after recording the receipt", async () => {
  let finish!: (value: unknown) => void;
  mocks.post
    .mockResolvedValueOnce({ data: { thread_id: "thread-a" } })
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Prompt B" },
  });
  finish({ data: { receipt_id: "receipt-a" } });
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledOnce());
  expect(view.state().new.text).toBe("Prompt B");
  expect(view.state()["thread-a"]).toMatchObject({
    text: "",
    receipt: "receipt-a",
    pending: false,
  });
});
it("retains unknown admission outcomes under the created id", async () => {
  mocks.post
    .mockResolvedValueOnce({ data: { thread_id: "thread-a" } })
    .mockRejectedValueOnce(new TypeError("Connection lost"));
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledOnce());
  expect(view.state()["thread-a"]).toMatchObject({
    text: "Prompt A",
    uncertain: true,
    pending: false,
  });
});
it("does not redirect after the originating conversation unmounts", async () => {
  let finish!: (value: unknown) => void;
  mocks.post
    .mockResolvedValueOnce({ data: { thread_id: "thread-a" } })
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const view = mount();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
  view.unmount();
  finish({ data: { receipt_id: "receipt-a" } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(mocks.navigate).not.toHaveBeenCalled();
});
