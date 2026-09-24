import { ToastProvider } from "a13n-ui";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { ErrorNotice, ErrorToast, InlineLoading, Loading } from ".";

const translate = vi.hoisted(
  () => (key: string, values?: Record<string, unknown>) =>
    key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
);
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

it("reads a limit refusal from its reason, since its message names identifiers", () => {
  const refusal = (reason: string) =>
    new ApiError(
      409,
      "conflict",
      "thread thr_1: mount limit",
      { kind: "thread", id: "thr_1", reason, limit: 32 },
      "req_test",
    );
  const { rerender } = render(<ErrorNotice error={refusal("mount_limit")} />);
  expect(
    screen.getByText(
      "A thread can mount at most 32 environments. Remove one before adding another.",
    ),
  ).toBeTruthy();
  rerender(<ErrorNotice error={refusal("idempotency_key_reused")} />);
  expect(
    screen.getByText(
      "This request was already sent with different content. Send it again as a new request.",
    ),
  ).toBeTruthy();
  // Any other refusal reads as the Service wrote it.
  rerender(<ErrorNotice error={refusal("inbox_full")} />);
  expect(screen.getByText("thread thr_1: mount limit")).toBeTruthy();
  expect(screen.getByText(/req_test/)).toBeTruthy();
});

it("explains a memory provider's refusals without its identifiers", () => {
  const { rerender } = render(
    <ErrorNotice
      error={
        new ApiError(
          409,
          "conflict",
          "memory mem_1: write not confirmed",
          { kind: "memory", id: "mem_1", reason: "write_unconfirmed" },
          "req_test",
        )
      }
    />,
  );
  expect(
    screen.getByText(
      "The memory's provider did not confirm this change, so it may or may not have happened. Reload to check before trying again.",
    ),
  ).toBeTruthy();
  rerender(
    <ErrorNotice
      error={
        new ApiError(
          503,
          "unavailable",
          "memory:mem0 unavailable",
          { dependency: "memory:mem0" },
          "req_test",
        )
      }
    />,
  );
  expect(
    screen.getByText(
      "The memory's provider is unavailable, so its records cannot be read or changed now. Try again later.",
    ),
  ).toBeTruthy();
});
