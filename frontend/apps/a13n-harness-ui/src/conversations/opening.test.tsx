// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useState } from "react";
import { ConversationOpening, useInitialReady } from "./opening";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function Page({ observed }: { observed: boolean }) {
  const [showAvailable, setShowAvailable] = useState(false);
  const ready = useInitialReady(observed || showAvailable);
  return (
    <ConversationOpening
      ready={ready}
      label="Opening conversation…"
      onContinue={() => setShowAvailable(true)}
    >
      <input aria-label="Draft" defaultValue="Retained input" />
    </ConversationOpening>
  );
}

it("mounts preparation behind one loading surface, then keeps content and focus through refreshes", () => {
  const view = render(<Page observed={false} />);
  const input = view.container.querySelector("input")!;
  expect(input.value).toBe("Retained input");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(input.closest("[inert]")).not.toBeNull();
  expect(screen.getByRole("status").textContent).toBe("Opening conversation…");
  view.rerender(<Page observed />);
  expect(screen.getByRole("textbox")).toBe(input);
  expect(input.closest("[inert]")).toBeNull();
  act(() => input.focus());
  view.rerender(<Page observed={false} />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("textbox")).toBe(input);
  expect(document.activeElement).toBe(input);
});

it("offers an explicit way to inspect a stalled initial connection without resubmitting", () => {
  vi.useFakeTimers();
  render(<Page observed={false} />);
  expect(screen.queryByRole("button")).toBeNull();
  act(() => vi.advanceTimersByTime(6000));
  fireEvent.click(
    screen.getByRole("button", { name: "Show available conversation" }),
  );
  expect(screen.queryByRole("status")).toBeNull();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(
    "Retained input",
  );
});
