// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Setup } from "./setup";
import type { Model } from "./client";
const mocks = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("./client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./client")>()),
  api: { POST: mocks.post, GET: mocks.get },
  result: (value: { data: unknown }) => value.data,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const status: Model<"SetupStatus"> = {
  needed: true,
  configuration_path: "/config/config.yaml",
  generation: "g",
  suggested_project_path: "/project",
  providers: [],
  agents: {},
  projects: {},
  project_paths: {},
  system_prompt: "Always included system foundation.",
  default_agent: null,
  default_project: null,
  environment_profile: "environment-native",
};
function mount(changes: Partial<Model<"SetupStatus">> = {}) {
  const close = vi.fn();
  const publication = vi.fn();
  render(
    <Setup
      status={{ ...status, ...changes }}
      reload={vi.fn().mockResolvedValue({ ...status, ...changes })}
      close={close}
      setApplying={publication}
    />,
  );
  return { close, publication };
}
function click(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
}
function skipToAgent() {
  click("Not now");
  click("Continue");
}
const preview = {
  generation: "g",
  files: { "agents/default.yaml": "kind: agent" },
  preserved_paths: [],
  project_paths: ["/project"],
  candidate_digest: "digest",
};
it("finishes all three steps after Not now without claiming a model connection", async () => {
  mocks.post
    .mockResolvedValueOnce({ data: preview })
    .mockResolvedValueOnce({ data: { completed: true, published_paths: [] } });
  const view = mount();
  expect(screen.queryByRole("combobox", { name: "Default agent" })).toBeNull();
  skipToAgent();
  expect(
    (
      screen.getByRole("combobox", {
        name: "Default agent",
      }) as HTMLSelectElement
    ).value,
  ).toBe("agent-default");
  expect(screen.getByText(status.system_prompt!)).toBeTruthy();
  click("Preview configuration");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Finish setup",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(mocks.post.mock.calls[0][1].body).toMatchObject({
    providers: [],
    api_key_model: null,
    default_agent: "agent-default",
    instructions: "",
  });
  click("Finish setup");
  await waitFor(() => expect(view.close).toHaveBeenCalledOnce());
  expect(view.publication).toHaveBeenCalledWith(true);
  expect(view.publication).toHaveBeenLastCalledWith(false);
});
it("keeps API-key references and additional instructions across Back navigation", async () => {
  mocks.post.mockResolvedValue({ data: preview });
  mount();
  fireEvent.click(screen.getByRole("radio", { name: "API key — BYOK" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Model route" }), {
    target: { value: "openai:gpt-5" },
  });
  fireEvent.change(
    screen.getByRole("textbox", { name: "API key environment variable" }),
    { target: { value: "MY_API_KEY" } },
  );
  click("Continue");
  click("Continue");
  fireEvent.change(
    screen.getByRole("textbox", { name: "Additional instructions (optional)" }),
    { target: { value: "Use concise answers." } },
  );
  click("Back");
  click("Back");
  expect(
    (
      screen.getByRole("textbox", {
        name: "API key environment variable",
      }) as HTMLInputElement
    ).value,
  ).toBe("MY_API_KEY");
  click("Continue");
  click("Continue");
  click("Preview configuration");
  await waitFor(() => expect(mocks.post).toHaveBeenCalledOnce());
  expect(mocks.post.mock.calls[0][1].body).toMatchObject({
    default_agent: "agent-api-key",
    api_key_model: {
      route: "openai:gpt-5",
      authentication: { kind: "api_key", env: "MY_API_KEY" },
    },
    instructions: "Use concise answers.",
  });
});
it("does not permit Sandbox continuation if a later root's probe fails", async () => {
  mocks.post
    .mockResolvedValueOnce({ data: { ready: true, message: "first passed" } })
    .mockRejectedValueOnce(new TypeError("offline"));
  mount({
    projects: { "project-local": "Local" },
    project_paths: { "project-local": ["/first", "/second"] },
  });
  click("Not now");
  fireEvent.click(
    screen.getByRole("radio", {
      name: "Sandbox — verify isolation before continuing",
    }),
  );
  click("Check Sandbox / Retry");
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Connection failed",
    ),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    mocks.post.mock.calls.map((call) => call[1].body.project_path),
  ).toEqual(["/first", "/second"]);
  fireEvent.click(
    screen.getByRole("radio", { name: "Full Control — no Sandbox" }),
  );
  click("Continue");
  expect(
    screen.getByRole("heading", { name: "Step 3 of 3: Your default Agent" }),
  ).toBeTruthy();
});
it("retains successful readiness while adding instructions, but invalidates it for changed roots", async () => {
  mocks.post.mockResolvedValue({ data: { ready: true, message: "passed" } });
  mount();
  click("Not now");
  fireEvent.click(
    screen.getByRole("radio", {
      name: "Sandbox — verify isolation before continuing",
    }),
  );
  click("Check Sandbox / Retry");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Continue",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  click("Continue");
  fireEvent.change(
    screen.getByRole("textbox", { name: "Additional instructions (optional)" }),
    { target: { value: "Brief answers" } },
  );
  click("Back");
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  fireEvent.change(
    screen.getByRole("textbox", { name: "New local project path" }),
    { target: { value: "/changed" } },
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
it("cancels a pending probe without enabling Sandbox", async () => {
  let aborted = false;
  mocks.post.mockImplementation(
    (_path, options) =>
      new Promise((_resolve, reject) =>
        options.signal.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("aborted", "AbortError"));
        }),
      ),
  );
  mount();
  click("Not now");
  fireEvent.click(
    screen.getByRole("radio", {
      name: "Sandbox — verify isolation before continuing",
    }),
  );
  click("Check Sandbox / Retry");
  click("Cancel check");
  await waitFor(() => expect(aborted).toBe(true));
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
