// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { ConversationConfiguration } from "./configuration";

vi.mock("../configuration/project-folders", () => ({
  ProjectFolders: () => null,
}));
vi.mock("../configuration/environment-bindings", () => ({
  EnvironmentBindings: () => null,
  BindingSummary: () => null,
}));
afterEach(cleanup);

it("previews Project environments without applying until the reviewed action is chosen", async () => {
  const configuration = {
    version: 3,
    project_id: "project-one",
    agent_source: { id: "agent-one" },
    environment_profile_id: "environment-native",
    local_roots: ["/old"],
  };
  const get = vi.fn(async (path: string) => ({
    data:
      path === "/api/threads/{thread_id}/configuration"
        ? {
            capture_source: "none",
            captured: null,
            next_run: { configuration },
          }
        : path === "/api/threads/{thread_id}/project-environments"
          ? {
              expected_version: 3,
              defaults_digest: "reviewed-digest",
              current: configuration,
              replacement: { ...configuration, local_roots: ["/new"] },
              patch: { local_roots: ["/new"] },
            }
          : path === "/api/projects"
            ? []
            : {},
  }));
  const post = vi.fn(async () => ({ data: {} }));
  const reconcile = vi.fn();
  const user = userEvent.setup();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TransportContext
        value={{ client: { GET: get, POST: post } } as unknown as Transport}
      >
        <MemoryRouter>
          <ConversationConfiguration
            threadId="thread-one"
            reconcile={reconcile}
          />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
  await user.click(
    await screen.findByRole("button", { name: "Preview Project environments" }),
  );
  const apply = await screen.findByRole("button", {
    name: "Apply reviewed environments",
  });
  expect(post).not.toHaveBeenCalled();
  expect(get).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/project-environments",
    { params: { path: { thread_id: "thread-one" } } },
  );
  await user.click(apply);
  await waitFor(() => expect(reconcile).toHaveBeenCalledTimes(1));
  expect(post).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/project-environments",
    {
      params: { path: { thread_id: "thread-one" } },
      body: { expected_version: 3, defaults_digest: "reviewed-digest" },
    },
  );
});
