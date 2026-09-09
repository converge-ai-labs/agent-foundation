// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserApp } from "./app";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function status(version = "1.2.3rc2") {
  return new Response(JSON.stringify({ api_version: "1", version }), {
    status: 200,
  });
}

it("consumes the fragment before fetching and displays the server package version", async () => {
  window.history.replaceState(null, "", "/#api_key=generated%2Bkey");
  const fetcher = vi.fn(() => {
    expect(window.location.hash).toBe("");
    return Promise.resolve(status());
  });
  vi.stubGlobal("fetch", fetcher);
  render(<BrowserApp />);
  await screen.findByText("1.2.3rc2");
  expect(fetcher).toHaveBeenCalledWith(
    "/api/status",
    expect.objectContaining({
      headers: { Authorization: "Bearer generated+key" },
      cache: "no-store",
    }),
  );
  expect(window.localStorage.getItem("a13n-harness-ui.api-key")).toBe(
    "generated+key",
  );
  expect(document.body.textContent).not.toContain("generated+key");
});

it("accepts a replacement key after authentication failure", async () => {
  window.localStorage.setItem("a13n-harness-ui.api-key", "old-key");
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response("", { status: 401 }))
    .mockResolvedValue(status("2.0.0"));
  vi.stubGlobal("fetch", fetcher);
  render(<BrowserApp />);
  await screen.findByText("Enter the API key printed by this server.");
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "new-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByText("2.0.0");
  expect(window.localStorage.getItem("a13n-harness-ui.api-key")).toBe(
    "new-key",
  );
});

it("supports explicit server auth bypass without inventing a frontend version", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(status("0.0.0")));
  render(<BrowserApp />);
  await screen.findByText("0.0.0");
  expect(fetch).toHaveBeenCalledWith(
    "/api/status",
    expect.objectContaining({ headers: {} }),
  );
});

it("reports unavailable and incompatible servers instead of displaying a guessed version", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{}", { status: 200 })),
  );
  render(<BrowserApp />);
  await screen.findByText(
    "This server returned an incompatible status response.",
  );
  expect(screen.queryByText(/Running version/)).toBeNull();
});

it("forgets the retained credential", async () => {
  window.localStorage.setItem("a13n-harness-ui.api-key", "remembered");
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(status())
      .mockResolvedValue(new Response("", { status: 401 })),
  );
  render(<BrowserApp />);
  await screen.findByText("1.2.3rc2");
  fireEvent.click(screen.getByRole("button", { name: "Forget API key" }));
  await waitFor(() =>
    expect(window.localStorage.getItem("a13n-harness-ui.api-key")).toBeNull(),
  );
  await screen.findByText("Enter the API key printed by this server.");
});
