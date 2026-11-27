// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext, useUpdateNotices } from "../transport/context";
import { UpdateNotices, useUpdateNoticeState } from "./update-notices";

const queryKey = ["update-notices"];
const advisory: Schema<"SecurityAdvisory"> = {
  id: "GHSA-cfff-gggg-hhhh",
  summary: "Security update <strong>available</strong>",
  severity: "high",
  updated_at: "2026-10-05T12:00:00Z",
  url: "https://github.com/converge-ai-labs/agent-foundation/security/advisories/GHSA-cfff-gggg-hhhh",
};
const snapshot: Schema<"UpdateNotices"> = {
  current_version: "1.9.0",
  latest_stable: {
    is_newer: true,
    version: "1.10.0",
    url: "https://github.com/converge-ai-labs/agent-foundation/releases/tag/release/a13n-harness-ui-v1.10.0",
  },
  install_method: "uv_tool",
  advisories: [advisory],
};

function About() {
  const notices = useUpdateNoticeState();
  return (
    <>
      <button aria-description={notices.indicator?.description}>Menu</button>
      <UpdateNotices
        notices={notices}
        installedVersion="1.9.0"
        disconnected={false}
      />
    </>
  );
}

function show(data: Schema<"UpdateNotices">) {
  const query = new QueryClient();
  query.setQueryData(queryKey, data);
  return {
    query,
    ...render(
      <QueryClientProvider client={query}>
        <TransportContext.Provider value={createTransport("test", vi.fn())}>
          <About />
        </TransportContext.Provider>
      </QueryClientProvider>,
    ),
  };
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("shows release and advisory links as external destinations and copies the upgrade command", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  show(snapshot);
  expect(screen.getByText("v1.9.0 · Connected")).toBeTruthy();
  const release = screen.getByRole("link", { name: "v1.10.0 available" });
  const report = screen.getByRole("link", { name: "View advisory on GitHub" });
  expect(release.getAttribute("href")).toBe(snapshot.latest_stable?.url);
  expect(report.getAttribute("href")).toBe(advisory.url);
  for (const link of [release, report]) {
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  }
  expect(screen.getByRole("alert").querySelector("strong")).toBeNull();
  expect(screen.getByText(advisory.summary)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Copy command" }));
  await screen.findByText("Copied");
  expect(writeText).toHaveBeenCalledExactlyOnceWith("a13n-harness-ui update");
});

it("dismisses the current release badge and advisory revision and restores a changed revision", async () => {
  const description = () =>
    screen
      .getByRole("button", { name: "Menu" })
      .getAttribute("aria-description");
  const { unmount } = show(snapshot);
  expect(description()).toBe("Security advisory for this installation");
  fireEvent.click(screen.getByRole("button", { name: "Hide on this browser" }));
  expect(description()).toBe("Harness UI update available");
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Hide badge" }));
  expect(description()).toBeNull();
  expect(screen.getByRole("link", { name: "v1.10.0 available" })).toBeTruthy();
  unmount();

  const { query } = show(snapshot);
  expect(description()).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  const latest = snapshot.latest_stable!;
  await act(() => {
    query.setQueryData(queryKey, {
      ...snapshot,
      latest_stable: { ...latest, version: "1.11.0" },
      advisories: [],
    });
  });
  await screen.findByRole("link", { name: "v1.11.0 available" });
  expect(description()).toBe("Harness UI update available");
  await act(() => {
    query.setQueryData(queryKey, {
      ...snapshot,
      advisories: [
        {
          ...advisory,
          severity: "critical",
          updated_at: "2026-10-06T12:00:00Z",
        },
      ],
    });
  });
  expect(await screen.findByText("Critical security advisory")).toBeTruthy();
  expect(description()).toBe("Security advisory for this installation");
});

it("shows manual package-manager guidance without offering an installer command", () => {
  show({ ...snapshot, install_method: "other" });
  expect(
    screen.getByText("Update with the original package manager."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Copy command" })).toBeNull();
});

it("does not request notices for a shared-view transport", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  function GuestNoticeQuery() {
    const query = useUpdateNotices();
    return <output>{query.fetchStatus}</output>;
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TransportContext.Provider
        value={createTransport("guest", vi.fn(), "share-test")}
      >
        <GuestNoticeQuery />
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
  expect(screen.getByText("idle")).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});
