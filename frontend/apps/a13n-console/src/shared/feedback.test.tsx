import { ToastProvider } from "a13n-ui";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ErrorToast } from "./feedback";

const translate = vi.hoisted(() => (key: string) => key);
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: translate }),
}));

afterEach(cleanup);

function view(error: unknown, mounted = true) {
  return (
    <ToastProvider closeLabel="Dismiss">
      {mounted && <ErrorToast error={error} />}
    </ToastProvider>
  );
}

it("updates an error toast without offering an action, then closes it when cleared", async () => {
  const { rerender } = render(view(new Error("First failure")));
  await waitFor(() =>
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("First failure"),
  );

  rerender(view(new Error("Latest failure")));
  await waitFor(() => {
    const content = document.querySelector('[role="alertdialog"]')?.textContent;
    expect(content).toContain("Latest failure");
    expect(content).not.toContain("First failure");
  });
  expect(
    [...document.querySelectorAll('[role="alertdialog"] button')].some(
      (button) => button.textContent === "Try again",
    ),
  ).toBe(false);

  rerender(view(null));
  await waitFor(() =>
    expect(document.querySelector('[role="alertdialog"]')).toBeNull(),
  );
});

it("closes an error toast when its owner unmounts", async () => {
  const { rerender } = render(view(new Error("Owned failure")));
  await waitFor(() =>
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("Owned failure"),
  );

  rerender(view(new Error("Owned failure"), false));
  await waitFor(() =>
    expect(document.querySelector('[role="alertdialog"]')).toBeNull(),
  );
});

it("keeps an error visible until its owner resolves it", () => {
  vi.useFakeTimers();
  try {
    render(view(new Error("Persistent failure")));
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("Persistent failure");

    act(() => vi.advanceTimersByTime(60_000));
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("Persistent failure");
  } finally {
    vi.useRealTimers();
  }
});
