// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { parse } from "yaml";
import { BrowserFields } from "./browser-fields";

const post = vi.hoisted(() => vi.fn());
vi.mock("../transport/context", () => ({
  useTransport: () => ({ client: { POST: post } }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const discovered = {
  browsers: [
    { name: "Chrome", path: "/opt/chrome", source: "system" },
    { name: "Chromium", path: "/opt/chromium", source: "system" },
  ],
  vncs: [
    { kind: "tigervnc", path: "/opt/Xvnc", source: "system" },
    { kind: "x11vnc", path: "/opt/x11vnc", source: "system" },
    { kind: "wayvnc", path: "/opt/wayvnc", source: "system" },
  ],
};
beforeEach(() => {
  post.mockImplementation(
    async (url: string, options?: { body: { vnc_executable: string } }) => {
      if (url === "/api/browser/scan") return { data: discovered };
      const kind =
        options?.body.vnc_executable === "/opt/x11vnc"
          ? "x11vnc"
          : options?.body.vnc_executable === "/opt/wayvnc"
            ? "wayvnc"
            : "tigervnc";
      return {
        data: {
          platform: "linux",
          supported: true,
          ready: kind === "tigervnc",
          vnc: { kind },
          requirements: [
            ...(kind === "tigervnc"
              ? [
                  {
                    name: "Display server",
                    required: true,
                    installed: true,
                    managed: true,
                    detail: "TigerVNC includes its display server",
                  },
                ]
              : []),
            {
              name: "Browser",
              required: true,
              installed: true,
              detail: "/opt/chrome",
            },
            {
              name: "VNC server",
              required: true,
              installed: true,
              detail: "/opt/Xvnc",
            },
            ...(kind === "tigervnc"
              ? [
                  {
                    name: "Desktop resizing",
                    required: false,
                    installed: true,
                    purpose: "xrandr enables Viewport size switching.",
                    detail: "/usr/bin/xrandr",
                  },
                ]
              : [
                  {
                    name: "Display server",
                    required: true,
                    installed: false,
                    purpose:
                      kind === "x11vnc"
                        ? "Xvfb provides a private display."
                        : "Sway provides a private desktop.",
                    detail:
                      kind === "x11vnc"
                        ? "Install Xvfb on PATH."
                        : "Install Sway on PATH.",
                  },
                ]),
          ],
          steps: [],
        },
      };
    },
  );
});
function show(
  initial = 'schema_version: "1"\nprocess: {log_level: DEBUG}\n',
  client = new QueryClient(),
  active = true,
) {
  function Fields() {
    const [source, setSource] = useState(initial);
    return (
      <>
        <BrowserFields source={source} onChange={setSource} active={active} />
        <output data-testid="source">{source}</output>
      </>
    );
  }
  return render(
    <QueryClientProvider client={client}>
      <Fields />
    </QueryClientProvider>,
  );
}
it("does not scan while the browser settings section is closed", () => {
  show(undefined, undefined, false);
  expect(post).not.toHaveBeenCalled();
});
it("scans on opening, selects discovered executables and exposes Other paths", async () => {
  const initial = 'schema_version: "1"\nprocess: {log_level: DEBUG}\n';
  let complete!: (value: unknown) => void;
  post.mockReturnValueOnce(
    new Promise((resolve) => {
      complete = resolve;
    }),
  );
  show(initial);
  const user = userEvent.setup();
  expect(post).toHaveBeenCalledOnce();
  expect(
    screen
      .getByRole("combobox", { name: "Browser executable" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("combobox", { name: "VNC executable" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen.queryByRole("textbox", { name: "Browser executable path" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Scanning…" }).hasAttribute("disabled"),
  ).toBe(true);
  complete({ data: discovered });
  await screen.findByRole("button", { name: "Scan" });
  expect(post).toHaveBeenCalledWith("/api/browser/scan");
  expect(
    within(
      screen.getByRole("list", { name: "Browser requirements" }),
    ).getByRole("combobox", { name: "Browser executable" }),
  ).toBeTruthy();
  expect(screen.getByTestId("source").textContent).toBe(initial);
  await user.click(
    screen.getByRole("combobox", { name: "Browser executable" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Chromium — /opt/chromium" }),
  );
  await user.click(screen.getByRole("combobox", { name: "VNC executable" }));
  await user.click(await screen.findByRole("option", { name: "Other" }));
  fireEvent.change(
    screen.getByRole("textbox", { name: "VNC executable path" }),
    { target: { value: "/custom/Xvnc" } },
  );
  expect(parse(screen.getByTestId("source").textContent!)).toEqual({
    schema_version: "1",
    process: { log_level: "DEBUG" },
    browser: { executable: "/opt/chromium", vnc_executable: "/custom/Xvnc" },
  });
  await user.click(screen.getByRole("combobox", { name: "VNC executable" }));
  await user.click(
    await screen.findByRole("option", { name: "TigerVNC Xvnc — /opt/Xvnc" }),
  );
  expect(
    screen.queryByRole("textbox", { name: "VNC executable path" }),
  ).toBeNull();
  expect(
    parse(screen.getByTestId("source").textContent!).browser.vnc_executable,
  ).toBe("/opt/Xvnc");
});
it("preserves saved custom paths, retries failed scans and rescans on settings visits", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 15000 } },
  });
  const initial = 'schema_version: "1"\nbrowser: {executable: /saved/chrome}\n';
  post.mockRejectedValueOnce(new Error("Scan failed"));
  const view = show(initial, client);
  const user = userEvent.setup();
  expect(
    (
      screen.getByRole("textbox", {
        name: "Browser executable path",
      }) as HTMLInputElement
    ).value,
  ).toBe("/saved/chrome");
  await screen.findByText("Scan failed");
  expect(
    screen
      .getByRole("combobox", { name: "Browser executable" })
      .hasAttribute("disabled"),
  ).toBe(true);
  post.mockResolvedValueOnce({ data: { browsers: [], vncs: [] } });
  await user.click(screen.getByRole("button", { name: "Scan" }));
  await screen.findByRole("button", { name: "Scan" });
  expect(
    screen
      .getByRole("combobox", { name: "Browser executable" })
      .hasAttribute("disabled"),
  ).toBe(false);
  expect(screen.getByTestId("source").textContent).toBe(initial);
  view.unmount();
  post.mockResolvedValueOnce({ data: discovered });
  show(initial, client);
  await screen.findByRole("button", { name: "Scan" });
  await waitFor(() =>
    expect(
      screen
        .getByRole("combobox", { name: "Browser executable" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(
    post.mock.calls.filter(([url]) => url === "/api/browser/scan"),
  ).toHaveLength(3);
});

it("checks unsaved VNC selections, switches requirements and preserves optional labels", async () => {
  show(
    'schema_version: "1"\nbrowser: {executable: /opt/chrome, vnc_executable: /opt/Xvnc}\n',
  );
  const user = userEvent.setup();
  await screen.findByText("/usr/bin/xrandr");
  expect(
    within(screen.getByText("Desktop resizing").closest("li")!).getByText(
      "Optional",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Desktop viewer")).toBeNull();
  expect(screen.queryByText("Display server")).toBeNull();
  expect(screen.queryByText(/Browsers: /)).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "VNC executable" }));
  await user.click(
    await screen.findByRole("option", { name: "x11vnc — /opt/x11vnc" }),
  );
  await screen.findByText("Install Xvfb on PATH.");
  expect(screen.queryByText("/usr/bin/xrandr")).toBeNull();
  expect(screen.getByText("Missing")).toBeTruthy();
  expect(post).toHaveBeenCalledWith(
    "/api/browser/check",
    expect.objectContaining({
      body: { executable: "/opt/chrome", vnc_executable: "/opt/x11vnc" },
    }),
  );
  await user.click(screen.getByRole("combobox", { name: "VNC executable" }));
  await user.click(
    await screen.findByRole("option", { name: "WayVNC — /opt/wayvnc" }),
  );
  await screen.findByText("Install Sway on PATH.");
  expect(screen.queryByText("Install Xvfb on PATH.")).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "VNC executable" }));
  await user.click(await screen.findByRole("option", { name: "Other" }));
  post.mockImplementation(async (url) => {
    if (url === "/api/browser/check") throw new Error("Invalid browser path");
    return { data: discovered };
  });
  fireEvent.change(
    screen.getByRole("textbox", { name: "VNC executable path" }),
    { target: { value: "/custom/invalid" } },
  );
  await screen.findByText("Invalid browser path");
  expect(screen.queryByText("Install Sway on PATH.")).toBeNull();
  expect(
    parse(screen.getByTestId("source").textContent!).browser.vnc_executable,
  ).toBe("/custom/invalid");
});

it("returns an explicit executable override to automatic selection", async () => {
  show('schema_version: "1"\nbrowser: {executable: /saved/chrome}\n');
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Scan" });
  await user.click(
    screen.getByRole("combobox", { name: "Browser executable" }),
  );
  await user.click(await screen.findByRole("option", { name: "Automatic" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/browser/check",
      expect.objectContaining({
        body: { executable: null, vnc_executable: null },
      }),
    ),
  );
  expect(
    parse(screen.getByTestId("source").textContent!).browser.executable,
  ).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "Browser executable path" }),
  ).toBeNull();
});
it("shows a platform explanation instead of unusable settings on unsupported Hosts", async () => {
  post.mockImplementation(async (url: string) => ({
    data: url.endsWith("/scan")
      ? discovered
      : {
          platform: "macos",
          supported: false,
          ready: false,
          requirements: [],
          message: "The Host browser currently requires Linux.",
        },
  }));
  show();
  await screen.findByText("The Host browser currently requires Linux.");
  expect(
    screen.queryByRole("combobox", { name: "Browser executable" }),
  ).toBeNull();
  expect(screen.queryByText(/Missing a program/)).toBeNull();
});
