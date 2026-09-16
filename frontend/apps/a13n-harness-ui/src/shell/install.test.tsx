// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { InstallProvider, InstallSettings } from "./install";

let display: EventTarget & { matches: boolean };
beforeEach(() => {
  display = Object.assign(new EventTarget(), { matches: false });
  vi.stubGlobal("matchMedia", () => display);
  vi.stubGlobal("isSecureContext", true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  return render(
    <InstallProvider>
      <InstallSettings />
    </InstallProvider>,
  );
}
function offer(prompt = vi.fn().mockResolvedValue({ outcome: "dismissed" })) {
  const event = Object.assign(
    new Event("beforeinstallprompt", { cancelable: true }),
    { prompt },
  );
  act(() => {
    window.dispatchEvent(event);
  });
  return { event, prompt };
}

it("retains an early install offer across navigation and prompts only on user action", async () => {
  function Page() {
    const [settings, setSettings] = useState(false);
    return (
      <InstallProvider>
        <button onClick={() => setSettings(!settings)}>Settings</button>
        {settings && <InstallSettings />}
      </InstallProvider>
    );
  }
  render(<Page />);
  const { event, prompt } = offer();
  expect(event.defaultPrevented).toBe(true);
  expect(prompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Settings"));
  fireEvent.click(screen.getByRole("button", { name: "Install app" }));
  await vi.waitFor(() =>
    expect(screen.queryByRole("button", { name: "Install app" })).toBeNull(),
  );
  expect(prompt).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByText("Settings"));
  fireEvent.click(screen.getByText("Settings"));
  expect(screen.queryByRole("button", { name: "Install app" })).toBeNull();
  expect(screen.queryByText(/Installed\./)).toBeNull();
});

it("does not report accepted prompts as completed installs and clears the offer on appinstalled", async () => {
  mount();
  offer(vi.fn().mockResolvedValue({ outcome: "accepted" }));
  fireEvent.click(screen.getByRole("button", { name: "Install app" }));
  await vi.waitFor(() =>
    expect(screen.queryByRole("button", { name: "Install app" })).toBeNull(),
  );
  expect(screen.queryByText(/Installed\./)).toBeNull();
  act(() => {
    window.dispatchEvent(new Event("appinstalled"));
  });
  expect(screen.getByRole("status").textContent).toContain("Installed.");
});

it("handles prompt failure without reusing the event, then accepts a fresh offer", async () => {
  mount();
  const { prompt } = offer(vi.fn().mockRejectedValue(new Error("blocked")));
  fireEvent.click(screen.getByRole("button", { name: "Install app" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "could not be opened",
  );
  expect(prompt).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "Install app" })).toBeNull();
  offer();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Install app" })).toBeTruthy();
});

it("discloses platform guidance, server dependency and insecure origins without a dead install button", () => {
  mount();
  expect(screen.getByText(/server must still be running/)).toBeTruthy();
  expect(screen.getByText(/On iPhone or iPad/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install app" })).toBeNull();
  cleanup();
  vi.stubGlobal("isSecureContext", false);
  mount();
  offer();
  expect(screen.getByText(/requires HTTPS/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install app" })).toBeNull();
});

it("recognizes standalone display and removes event listeners on unmount", () => {
  const remove = vi.spyOn(window, "removeEventListener");
  const page = mount();
  act(() => {
    display.matches = true;
    display.dispatchEvent(new Event("change"));
  });
  offer();
  expect(screen.getByText("Running in an app window.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Install app" })).toBeNull();
  page.unmount();
  expect(remove).toHaveBeenCalledWith(
    "beforeinstallprompt",
    expect.any(Function),
  );
  expect(remove).toHaveBeenCalledWith("appinstalled", expect.any(Function));
});

it("recognizes the iOS standalone flag", () => {
  vi.stubGlobal("navigator", { standalone: true });
  mount();
  expect(screen.getByText("Running in an app window.")).toBeTruthy();
});
