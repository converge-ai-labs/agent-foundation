// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { i18n } from "../i18n";
import { ApiError } from "../transport/client";
import { ConversationUnavailable } from "./conversation-unavailable";

afterEach(cleanup);

it("translates client fallback errors on language change without translating identical server messages", async () => {
  const props = { retry: vi.fn(), retrying: false, hasDraft: true };
  const { rerender } = render(
    <MemoryRouter>
      <ConversationUnavailable
        {...props}
        error={new ApiError("Request failed (500).", 500, undefined, true)}
      />
    </MemoryRouter>,
  );
  try {
    expect(screen.getByText("Request failed (500).")).toBeTruthy();
    await act(() => i18n.changeLanguage("zh-CN"));
    expect(screen.getByText("请求失败（500）。")).toBeTruthy();
    rerender(
      <MemoryRouter>
        <ConversationUnavailable
          {...props}
          error={new ApiError("Request failed (500).", 500)}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Request failed (500).")).toBeTruthy();
  } finally {
    await act(() => i18n.changeLanguage("en"));
  }
});
