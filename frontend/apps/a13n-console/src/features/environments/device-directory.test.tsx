import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DeviceDirectory } from "./device-directory";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
beforeEach(() => {
  http.GET.mockReset();
});

function setup(initial = "") {
  const onChange = vi.fn();
  function Editor() {
    const [value, setValue] = useState(initial);
    return (
      <DeviceDirectory
        environmentId="env_device"
        value={value}
        onChange={(path) => {
          onChange(path);
          setValue(path);
        }}
      />
    );
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <Editor />
    </QueryClientProvider>,
  );
  return { ...view, onChange, cache, user: userEvent.setup() };
}

it("browses bounded pages and parent paths, saving only the explicitly selected directory", async () => {
  http.GET.mockImplementation(async (path, { params }) => ({
    data: path.endsWith("/device")
      ? {
          default_working_directory: "/work",
          directory_discovery: true,
          path_style: "posix",
        }
      : params.query.path === "/work/beta"
        ? {
            path: "/work/beta",
            parent_path: "/work",
            entries: [],
          }
        : {
            path: "/work",
            parent_path: "/",
            entries: params.query.offset
              ? [{ name: "beta", path: "/work/beta" }]
              : [{ name: "alpha", path: "/work/alpha" }],
            next_offset: params.query.offset ? null : 50,
          },
  }));
  const { user, onChange } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Browse directories" }),
  );
  await user.click(await screen.findByRole("button", { name: "Next page" }));
  await user.click(await screen.findByRole("button", { name: "beta" }));
  await screen.findByText("No subdirectories.");
  expect(onChange).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Parent directory" }));
  await screen.findByRole("button", { name: "alpha" });
  await user.click(
    screen.getByRole("button", { name: "Select this directory" }),
  );
  expect(onChange).toHaveBeenLastCalledWith("/work");
  expect(screen.queryByRole("button", { name: "Close browser" })).toBeNull();
  expect(http.GET).toHaveBeenCalledWith(
    expect.stringContaining("/directories"),
    expect.objectContaining({
      params: {
        path: { environment_id: "env_device" },
        query: { path: "/work", offset: 50, limit: 50 },
      },
      signal: expect.any(AbortSignal),
    }),
  );
});

it("keeps default and manual paths available when directory discovery is disabled", async () => {
  http.GET.mockResolvedValue({
    data: {
      default_working_directory: "/C:/work",
      directory_discovery: false,
      path_style: "windows",
    },
  });
  const { user, onChange } = setup();
  await screen.findByText(
    "Browsing is disabled on this Device. Use its default or enter a known path.",
  );
  expect(
    screen.queryByRole("button", { name: "Browse directories" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Use Device default" }));
  expect(onChange).toHaveBeenLastCalledWith("/C:/work");
  await user.clear(screen.getByRole("textbox", { name: "Working directory" }));
  await user.type(
    screen.getByRole("textbox", { name: "Working directory" }),
    "/D:/project",
  );
  expect(onChange).toHaveBeenLastCalledWith("/D:/project");
  expect(http.GET.mock.calls.every(([path]) => path.endsWith("/device"))).toBe(
    true,
  );
});

it("preserves the chosen path on offline and directory errors without substituting a default", async () => {
  http.GET.mockRejectedValue(new Error("Device is offline"));
  const { user, onChange } = setup("/saved");
  await screen.findByText("Device is offline");
  expect(onChange).not.toHaveBeenCalled();
  expect(
    (
      screen.getByRole("textbox", {
        name: "Working directory",
      }) as HTMLInputElement
    ).value,
  ).toBe("/saved");
  await user.type(
    screen.getByRole("textbox", { name: "Working directory" }),
    "/known",
  );
  expect(onChange).toHaveBeenLastCalledWith("/saved/known");
});

it("cancels in-flight browsing on close", async () => {
  let signal: AbortSignal | undefined;
  http.GET.mockImplementation((path, args) =>
    path.endsWith("/device")
      ? Promise.resolve({
          data: {
            default_working_directory: "/work",
            directory_discovery: true,
          },
        })
      : new Promise(() => {
          signal = args.signal;
        }),
  );
  const { user } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Browse directories" }),
  );
  await waitFor(() => expect(signal).toBeDefined());
  await user.click(screen.getByRole("button", { name: "Close browser" }));
  await waitFor(() => expect(signal?.aborted).toBe(true));
});
