import { ToastProvider } from "a13n-ui";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ErrorToast } from "./feedback";

const translate = vi.hoisted(() => (key: string) => key);
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: translate }),
}));

afterEach(cleanup);

function view(error: unknown, retry?: () => void, mounted = true) {
  return (
    <ToastProvider closeLabel="Dismiss">
      {mounted && <ErrorToast error={error} retry={retry} />}
    </ToastProvider>
  );
}

it("updates an error toast and its action, then closes it when cleared", async () => {
  const firstRetry = vi.fn();
  const latestRetry = vi.fn();
  const { rerender } = render(view(new Error("First failure"), firstRetry));
  await waitFor(() =>
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("First failure"),
  );

  rerender(view(new Error("Latest failure"), latestRetry));
  await waitFor(() => {
    const content = document.querySelector('[role="alertdialog"]')?.textContent;
    expect(content).toContain("Latest failure");
    expect(content).not.toContain("First failure");
  });
  const action = [
    ...document.querySelectorAll('[role="alertdialog"] button'),
  ].find((button) => button.textContent === "Try again");
  expect(action).toBeDefined();
  await userEvent.click(action!);
  expect(latestRetry).toHaveBeenCalledOnce();
  expect(firstRetry).not.toHaveBeenCalled();

  rerender(view(null, latestRetry));
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

  rerender(view(new Error("Owned failure"), undefined, false));
  await waitFor(() =>
    expect(document.querySelector('[role="alertdialog"]')).toBeNull(),
  );
});

it("keeps an actionable error visible until its owner resolves it", () => {
  vi.useFakeTimers();
  try {
    render(view(new Error("Persistent failure"), vi.fn()));
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
