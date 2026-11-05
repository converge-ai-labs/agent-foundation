// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { i18n } from "../i18n";
import { ApiError, NetworkError } from "../transport/client";
import { ConnectionNotice, ConnectionNoticeContext } from "./connection";
import { ErrorNotice } from "./ui";

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

it("consolidates connection errors without hiding actionable local failures", () => {
  const retry = vi.fn();
  render(
    <ConnectionNoticeContext.Provider value={true}>
      <ConnectionNotice retry={retry} />
      <ErrorNotice error={new NetworkError(new TypeError("Failed to fetch"))} />
      <ErrorNotice error={new ApiError("Bad gateway", 502)} />
      <ErrorNotice error={new ApiError("Unavailable", 503)} />
      <ErrorNotice error={new ApiError("Gateway timeout", 504)} />
      <ErrorNotice error={new ApiError("Permission denied", 403)} />
      <ErrorNotice error={new ApiError("Invalid configuration", 422)} />
      <ErrorNotice error={new Error("Draft could not be synchronized")} />
    </ConnectionNoticeContext.Provider>,
  );
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getAllByRole("alert").map((node) => node.textContent)).toEqual([
    "Permission denied",
    "Invalid configuration",
    "Draft could not be synchronized",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("keeps isolated network errors visible when there is no global notice", () => {
  const retry = vi.fn();
  render(<ErrorNotice error={new NetworkError(null)} retry={retry} />);
  expect(screen.getByRole("alert").textContent).toContain(
    "Unable to reach the server",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("localizes client-generated API failures while retaining identical server messages", async () => {
  const message = "Request failed (500).";
  const retry = vi.fn();
  render(
    <>
      <ErrorNotice
        error={new ApiError(message, 500, undefined, true)}
        retry={retry}
      />
      <ErrorNotice error={new ApiError(message, 500)} />
    </>,
  );
  expect(screen.getAllByText(message)).toHaveLength(2);
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(screen.getByText(message)).toBeTruthy();
  expect(screen.getByText("请求失败（500）。")).toBeTruthy();
  expect(retry).not.toHaveBeenCalled();
});
