// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { DraftContext, SourceDocument, type SourceDraft } from "./sources";

vi.mock("./editor", () => ({ SourceEditor: () => null }));
vi.mock("./fields", () => ({ ResourceFields: () => null }));
afterEach(cleanup);

it("retains unsaved configuration on cancel and navigates only after confirmed discard", async () => {
  const path = "models/new.yaml";
  const draft: SourceDraft = {
    content: "name: My unsaved model\n",
    base: null,
    digest: null,
    replacement: false,
  };
  const drafts = new Map([[path, draft]]);
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client: {} } as Transport}>
        <DraftContext value={drafts}>
          <MemoryRouter initialEntries={["/settings/source"]}>
            <Routes>
              <Route
                path="/settings/source"
                element={<SourceDocument path={path} isNew />}
              />
              <Route path="/settings/resources" element={<h1>Resources</h1>} />
            </Routes>
          </MemoryRouter>
        </DraftContext>
      </TransportContext>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  let dialog = await screen.findByRole("dialog", {
    name: "Discard this unsaved configuration?",
  });
  expect(dialog.textContent).toContain(path);
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(drafts.get(path)).toBe(draft);
  expect(screen.queryByRole("heading", { name: "Resources" })).toBeNull();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  dialog = await screen.findByRole("dialog", {
    name: "Discard this unsaved configuration?",
  });
  await user.click(
    within(dialog).getByRole("button", { name: "Discard draft" }),
  );
  await screen.findByRole("heading", { name: "Resources" });
  expect(drafts.has(path)).toBe(false);
});
