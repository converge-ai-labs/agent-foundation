import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { RunOptions } from "./option-chips";
import { useRunOptions } from "./options-dialog";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
    basePath: "/workspace/design",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      Object.entries(values ?? {}).reduce(
        (text, [name, value]) => text.replaceAll(`{{${name}}}`, value),
        key,
      ),
  }),
}));

const vision = {
  id: "mdl_0123456789abcdef0123",
  key: "vision",
  name: "Vision",
  enabled: true,
  provider_id: "provider",
  config: {
    model_name: "claude-vision",
    characteristics: { capabilities: ["image_understanding"] },
  },
};

function mount(submit: (options: unknown) => void) {
  function Editor() {
    const options = useRunOptions();
    return (
      <>
        <RunOptions options={options} />
        <button onClick={() => submit(options.build())}>Submit</button>
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <Editor />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("distinguishes new allocation from reuse and submits the selected identity", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    response: new Response(null),
    data: {
      items: path.includes("environment-templates")
        ? [{ id: "envt_test", name: "Research" }]
        : path.endsWith("/environments")
          ? [
              { id: "env_first", name: "Research", status: "ready" },
              { id: "env_second", name: "Research", status: "stopped" },
              { id: "env_gone", name: "Research", status: "deleted" },
            ]
          : [],
      next_cursor: null,
    },
  }));
  const submit = vi.fn();
  mount(submit);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  // Without a choice the agent's own template is reserved; there is no "none".
  expect(screen.queryByRole("option", { name: "No environment" })).toBeNull();
  await user.click(
    await screen.findByRole("option", {
      name: "Create from template: Research",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(
    screen.getByRole("button", {
      name: "Environment: Create from template: Research",
    }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ environment: { template_id: "envt_test" } }),
  );
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  expect(
    await screen.findByRole("option", {
      name: "Reuse existing: Research (env_first)",
    }),
  ).toBeTruthy();
  // A deleted environment is never mounted again.
  expect(
    screen.queryByRole("option", {
      name: "Reuse existing: Research (env_gone)",
    }),
  ).toBeNull();
  await user.click(
    screen.getByRole("option", {
      name: "Reuse existing: Research (env_second)",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ environment: { environment_id: "env_second" } }),
  );
});

it("mounts a Device at a working directory and clears it when switching targets", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    response: new Response(null),
    data: {
      items: path.endsWith("/environments")
        ? [
            {
              id: "env_device",
              name: "Device",
              status: "ready",
              device_id: "native-device",
            },
          ]
        : [],
      next_cursor: null,
    },
  }));
  const submit = vi.fn();
  mount(submit);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Reuse existing: Device (env_device)",
    }),
  );
  await user.type(
    await screen.findByRole("textbox", { name: "Working directory" }),
    "/work/repo",
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      environment: {
        environment_id: "env_device",
        working_directory: "/work/repo",
      },
    }),
  );
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(await screen.findByRole("option", { name: "Inherit" }));
  expect(
    screen.queryByRole("textbox", { name: "Working directory" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].environment).toBeUndefined();
});

it("overrides media understanding per kind and names the choice on its chip", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    response: new Response(null),
    data: path.endsWith("/media-understanding-defaults")
      ? {
          id: "ws_test",
          version: 0,
          image: "mdl_0123456789abcdef0123",
          video: null,
          audio: null,
        }
      : {
          items: path.endsWith("/model-providers")
            ? [{ id: "provider", name: "Local", enabled: true }]
            : path.endsWith("/models")
              ? [vision]
              : [],
          next_cursor: null,
        },
  }));
  const submit = vi.fn();
  mount(submit);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(
    await screen.findByRole("button", { name: /Media understanding/ }),
  );
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  await waitFor(() => expect(image.hasAttribute("disabled")).toBe(false));
  await user.click(image);
  // The inherited option names the workspace default the run would fall back to.
  expect(
    (await screen.findByRole("option", { name: /^Inherit/ })).textContent,
  ).toContain("Vision");
  await user.click(await screen.findByRole("option", { name: /^Vision/ }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(screen.getByRole("button", { name: "Image: Vision" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].options).toEqual({
    overrides: {
      media_understanding: {
        image: "mdl_0123456789abcdef0123",
        video: null,
        audio: null,
      },
    },
  });
  // Opened without a media chip, the rows stay behind a summary of the selection.
  await user.click(screen.getByRole("button", { name: "Options" }));
  await screen.findByRole("button", {
    name: /Media understanding Image · Vision, others inherited/,
  });
  expect(
    screen.queryByRole("combobox", { name: "Image understanding" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Image: Vision" }));
  await user.click(
    await screen.findByRole("combobox", { name: "Image understanding" }),
  );
  await user.click(await screen.findByRole("option", { name: /^Inherit/ }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(screen.queryByRole("button", { name: /^Image:/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].options).toBeUndefined();
});

it("sends the chosen model and instructions as the run's overrides", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    response: new Response(null),
    data: {
      items: path.endsWith("/model-providers")
        ? [{ id: "provider", name: "Local", enabled: true }]
        : path.endsWith("/models")
          ? [vision]
          : [],
      next_cursor: null,
    },
  }));
  const submit = vi.fn();
  mount(submit);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: /^Vision/ }));
  await user.click(
    screen.getByRole("switch", { name: "Override instructions" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Instructions override" }),
    "Answer briefly.",
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(screen.getByRole("button", { name: "Model: Vision" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0]).toEqual({
    options: {
      overrides: {
        model: { model_id: "mdl_0123456789abcdef0123" },
        instructions: "Answer briefly.",
      },
    },
  });
});
