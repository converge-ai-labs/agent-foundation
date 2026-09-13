import { ToastProvider } from "a13n-ui";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ErrorToast, InlineLoading, Loading } from "./feedback";

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

it("exposes one accessible status for skeleton loading", () => {
  render(<Loading variant="table" columns={3} rows={2} />);

  const status = screen.getByRole("status");
  expect(status.getAttribute("aria-busy")).toBe("true");
  expect(status.textContent).toContain("Loading…");
  expect(status.querySelectorAll('[data-slot="skeleton"]')).not.toHaveLength(0);
  expect(status.querySelector(':scope > [aria-hidden="true"]')).not.toBeNull();
});

it("keeps inline loading compact and accessible", () => {
  render(<InlineLoading width="5rem" />);

  const status = screen.getByRole("status");
  expect(status.getAttribute("aria-busy")).toBe("true");
  expect(status.textContent).toContain("Loading…");
  expect(
    status.querySelector<HTMLElement>('[data-slot="skeleton"]')?.style.width,
  ).toBe("5rem");
});

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
