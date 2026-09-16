import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { PendingFeedback } from "./pending";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("lists pending actions without response controls", () => {
  render(
    <PendingFeedback
      actions={
        [
          {
            call_id: "first",
            kind: "approval",
            tool_name: "First action",
            provider_type: null,
            presentation: null,
          },
          {
            call_id: "second",
            kind: "client_tool",
            tool_name: "Second action",
            provider_type: null,
            presentation: null,
          },
        ] as Schema["PendingActionResource"][]
      }
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Waiting for a response" }),
  ).toBeTruthy();
  expect(screen.getByText("First action")).toBeTruthy();
  expect(screen.getByText("Second action")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Submit responses" })).toBeNull();
});
