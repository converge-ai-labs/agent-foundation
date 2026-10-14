import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { ReviewerSettings } from "./reviewer";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
const models = [
  { key: "review-model", name: "Reviewer", config: { model_api: "test" } },
] as Schema["Model"][];

it("requires a model, validates advanced settings, and applies without publishing", async () => {
  const onApply = vi.fn(),
    onClose = vi.fn();
  const user = userEvent.setup();
  render(
    <ReviewerSettings
      initial={undefined}
      models={models}
      readOnly={false}
      settingsSchemas={{
        test: {
          type: "object",
          additionalProperties: false,
          properties: { temperature: { type: "number" } },
        },
      }}
      onApply={onApply}
      onClose={onClose}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(
    await screen.findByText(
      "Choose a review model before saving automatic review permissions.",
    ),
  ).toBeTruthy();
  expect(onApply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("combobox", { name: "Review model" }));
  await user.click(await screen.findByRole("option", { name: "Reviewer" }));
  const settings = screen.getByLabelText("Review model settings (JSON)");
  await user.clear(settings);
  await user.paste('{"unknown": true}');
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(
    await screen.findByText(/must NOT have additional properties/),
  ).toBeTruthy();
  expect(onClose).not.toHaveBeenCalled();
  await user.clear(settings);
  await user.paste('{"temperature": 0.2}');
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).toHaveBeenCalledWith({
    model: "review-model",
    model_settings: { temperature: 0.2 },
  });
  expect(onClose).toHaveBeenCalledOnce();
});

it("keeps existing wildcard rules while editing one tool and discards cancelled edits", async () => {
  const onApply = vi.fn(),
    onClose = vi.fn();
  const initial = {
    model: "review-model",
    rules: { "mcp/source/*": { on_flagged: "approval_required" as const } },
    on_error: "allow" as const,
  };
  const props = {
    initial,
    models,
    readOnly: false,
    settingsSchemas: {},
    onApply,
    onClose,
    target: { selector: "shell.exec", label: "shell_exec" },
  };
  const user = userEvent.setup();
  const view = render(<ReviewerSettings {...props} />);
  await user.type(
    screen.getByLabelText("Custom review instructions"),
    "Temporary edit",
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onApply).not.toHaveBeenCalled();
  view.unmount();
  render(<ReviewerSettings {...props} />);
  expect(
    (screen.getByLabelText("Custom review instructions") as HTMLTextAreaElement)
      .value,
  ).toBe("");
  const thresholds = screen.getAllByRole("combobox", {
    name: "Rule risk threshold",
  });
  await user.click(thresholds[1]!);
  await user.click(await screen.findByRole("option", { name: "High risk" }));
  await user.click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).toHaveBeenCalledWith({
    ...initial,
    rules: { ...initial.rules, "shell.exec": { risk_threshold: "high" } },
  });
});

it("allows read-only inspection of advanced settings without editing", async () => {
  const onApply = vi.fn();
  const user = userEvent.setup();
  render(
    <ReviewerSettings
      initial={{ model: "review-model", shell_instruction: "Inspect writes" }}
      models={models}
      readOnly
      settingsSchemas={{}}
      onApply={onApply}
      onClose={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: "Apply to draft" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Advanced settings" }));
  await waitFor(() => expect(screen.getByText("Inspect writes")).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Add review rule" })).toBeNull();
  expect(onApply).not.toHaveBeenCalled();
});

it("does not add a tool override merely by inspecting and applying its settings", async () => {
  const initial = {
    model: "review-model",
    rules: { "shell.*": { risk_threshold: "high" as const } },
  };
  const onApply = vi.fn();
  render(
    <ReviewerSettings
      initial={initial}
      models={models}
      readOnly={false}
      settingsSchemas={{}}
      target={{ selector: "shell.exec", label: "shell_exec" }}
      onApply={onApply}
      onClose={vi.fn()}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Apply to draft" }));
  expect(onApply).toHaveBeenCalledWith(initial);
});
