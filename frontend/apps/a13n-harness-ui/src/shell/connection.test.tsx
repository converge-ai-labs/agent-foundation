// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApiError, NetworkError } from "../transport/client";
import { ConnectionNotice, ConnectionNoticeContext } from "./connection";
import { ErrorNotice } from "./ui";

afterEach(cleanup);

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
