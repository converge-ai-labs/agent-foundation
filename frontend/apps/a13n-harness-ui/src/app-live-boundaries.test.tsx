// @vitest-environment jsdom
import { useContext } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, Route, Routes } from "react-router";
import { BrowserApp } from "./app";
import { FileBuffer, FileBuffers } from "./native/buffer";

// Keep the real application lifetime and buffer ownership, without mounting the
// native file editor. Route changes must not remove its unload protection.
vi.mock("./shell/workbench", () => ({
  Workbench: function Workbench() {
    const files = useContext(FileBuffers);
    return (
      <>
        <Link to="/live">Live</Link>
        <Routes>
          <Route
            path="/"
            element={
              <button
                onClick={() => {
                  const buffer = new FileBuffer({
                    entry: {
                      path: "/note.txt",
                      revision: "one",
                      kind: "file",
                      size: 5,
                      modified_ns: 0,
                      mode: 420,
                    },
                    resolved_path: "/note.txt",
                    presentation: "text",
                    text: "saved",
                  });
                  buffer.value = "unsaved";
                  files.set("/note.txt", buffer);
                }}
              >
                Edit file
              </button>
            }
          />
          <Route
            path="/live"
            element={
              <>
                <h1>Live route</h1>
                <button onClick={() => files.clear()}>Discard buffer</button>
              </>
            }
          />
        </Routes>
      </>
    );
  },
}));
vi.mock("./shell/notifications", () => ({
  NotificationsProvider: ({ children }: { children: React.ReactNode }) =>
    children,
}));
vi.mock("./shell/startup", () => ({ Startup: () => null }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("retains unsaved file unload protection after navigating to Live", async () => {
  window.history.replaceState(null, "", "/");
  localStorage.setItem("a13n-harness-ui.api-key", "test");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            api_version: "1",
            version: "1",
            app: { state: "ready" },
            features: {},
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
    ),
  );
  render(<BrowserApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit file" }));
  fireEvent.click(screen.getByRole("link", { name: "Live" }));
  await screen.findByRole("heading", { name: "Live route" });
  const dirty = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Discard buffer" }));
  const clean = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
});
