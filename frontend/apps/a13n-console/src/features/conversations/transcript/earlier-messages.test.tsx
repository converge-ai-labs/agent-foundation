// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { EarlierMessages } from "./earlier-messages";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("preserves the existing message position when an earlier page is prepended", () => {
  const loadEarlier = vi.fn(async () => {});
  const view = (loading: boolean, more = true) => (
    <div data-session-stage>
      <EarlierMessages
        hasEarlier={more}
        loadingEarlier={loading}
        loadEarlier={loadEarlier}
      />
      <article data-message-id="visible">Existing message</article>
    </div>
  );
  const { container, rerender } = render(view(false));
  const viewport = container.querySelector<HTMLElement>(
    "[data-session-stage]",
  )!;
  const item = screen.getByText("Existing message");
  const rect = vi
    .spyOn(item, "getBoundingClientRect")
    .mockReturnValue({ top: 100 } as DOMRect);
  viewport.scrollTop = 200;
  fireEvent.click(
    screen.getByRole("button", { name: "Load earlier messages" }),
  );
  expect(loadEarlier).toHaveBeenCalledTimes(1);
  expect(viewport.dataset.loadingEarlier).toBe("true");
  rerender(view(true));
  rect.mockReturnValue({ top: 400 } as DOMRect);
  rerender(view(false, false));
  expect(viewport.scrollTop).toBe(500);
  expect(viewport.dataset.loadingEarlier).toBeUndefined();
  expect(screen.queryByRole("button")).toBeNull();
});
