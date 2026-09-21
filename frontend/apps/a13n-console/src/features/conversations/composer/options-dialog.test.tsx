import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RunOptions } from "./option-chips";
import { useRunOptions } from "./options-dialog";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("distinguishes new allocation from reuse and submits the selected identity", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.includes("environment-templates")
        ? [{ id: "envt_test", name: "Research" }]
        : path.endsWith("/environments")
          ? [
              { id: "env_first", name: "Research" },
              { id: "env_second", name: "Research" },
            ]
          : [],
    },
  }));
  const submit = vi.fn();
  function ComposerOptions() {
    const options = useRunOptions();
    return (
      <>
        <RunOptions options={options} />
        <button onClick={() => submit(options.build())}>Submit</button>
      </>
    );
  }
  const cache = new QueryClient();
  render(
    <QueryClientProvider client={cache}>
      <ComposerOptions />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Create from template: Research",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
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
  cache.clear();
});

it("preserves a captured directory and clears it when switching targets", async () => {
  http.GET.mockResolvedValue({
    data: {
      items: [
        {
          id: "env_saved",
          name: "Device",
          device_id: "native-device",
        },
      ],
    },
  });
  const submit = vi.fn();
  function Editor() {
    const options = useRunOptions({
      environment: {
        environment_id: "env_saved",
        working_directory: "/captured",
      },
    });
    return (
      <>
        <RunOptions options={options} />
        <button onClick={() => submit(options.build())}>Submit</button>
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Editor />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      environment: {
        environment_id: "env_saved",
        working_directory: "/captured",
      },
    }),
  );
  await user.click(screen.getByRole("button", { name: "Options" }));
  await screen.findByRole("textbox", { name: "Working directory" });
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(await screen.findByRole("option", { name: "Inherit" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].environment).toBeUndefined();
});

it("submits a Device default as an explicit path without access presets", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/device")
      ? {
          default_working_directory: "/selected/default",
          directory_discovery: false,
        }
      : {
          items: path.endsWith("/environments")
            ? [
                {
                  id: "env_device",
                  name: "Device",
                  device_id: "native-device",
                },
              ]
            : [],
        },
  }));
  const submit = vi.fn();
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
      <Editor />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Options" }));
  await user.click(screen.getByRole("combobox", { name: "Environment" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Reuse existing: Device (env_device)",
    }),
  );
  await screen.findByText(
    "Browsing is disabled on this Device. Use its default or enter a known path.",
  );
  await user.click(screen.getByRole("button", { name: "Use Device default" }));
  expect(
    screen.queryByRole("combobox", { name: "Access permissions" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({
      environment: {
        environment_id: "env_device",
        working_directory: "/selected/default",
      },
    }),
  );
});

it("edits media overrides through selectors and restores inheritance without advanced JSON", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.endsWith("/model-providers")
        ? [{ id: "provider", enabled: true }]
        : path.endsWith("/models")
          ? [
              {
                key: "vision",
                name: "Vision",
                enabled: true,
                provider_id: "provider",
                declarations: { capabilities: ["image_understanding"] },
              },
            ]
          : [],
    },
  }));
  const submit = vi.fn();
  function Editor() {
    const options = useRunOptions({
      config_override: {
        media_understanding: { image: "unavailable", audio: null },
      },
    });
    return (
      <>
        <RunOptions options={options} />
        <button onClick={() => submit(options.build())}>Submit</button>
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Editor />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Media understanding" }));
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  await user.click(image);
  await user.click(await screen.findByRole("option", { name: "Vision" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].config_override).toEqual({
    media_understanding: { image: "vision", audio: null },
  });
  await user.click(screen.getByRole("button", { name: "Media understanding" }));
  await user.click(
    screen.getByRole("combobox", { name: "Image understanding" }),
  );
  await user.click(await screen.findByRole("option", { name: "Inherit" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submit.mock.lastCall?.[0].config_override).toBeUndefined();
});
