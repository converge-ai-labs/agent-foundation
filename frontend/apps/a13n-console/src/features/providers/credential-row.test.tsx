// @vitest-environment jsdom
import { Input } from "a13n-ui";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { CredentialRow } from "./credential-row";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function Row({ configured }: { configured: boolean }) {
  const [secret, setSecret] = useState("");
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <CredentialRow
        label="API key"
        configured={configured}
        removing={removing}
        onRemovingChange={setRemoving}
        onDiscard={() => setSecret("")}
      >
        <Input
          aria-label="API key"
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
        />
      </CredentialRow>
      <p data-testid="draft">{secret || "empty"}</p>
    </>
  );
}

it("replaces, cancels and removes a saved secret without leaking the draft", async () => {
  const user = userEvent.setup();
  render(<Row configured />);
  expect(screen.getByText("Saved")).toBeTruthy();
  expect(screen.queryByLabelText("API key")).toBeNull();

  await user.click(screen.getByRole("button", { name: "Replace" }));
  await user.type(screen.getByLabelText("API key"), "new-secret");
  expect(screen.getByTestId("draft").textContent).toBe("new-secret");

  // Dismissing the expanded input discards what was typed into it.
  await user.click(screen.getByRole("button", { name: "Keep the saved key" }));
  expect(screen.queryByLabelText("API key")).toBeNull();
  expect(screen.getByTestId("draft").textContent).toBe("empty");
  expect(screen.getByText("Saved")).toBeTruthy();

  await user.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.getByText("Removed when you save")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Replace" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "Keep" }));
  expect(screen.getByText("Saved")).toBeTruthy();
});

it("offers to add a secret the provider does not hold yet", async () => {
  const user = userEvent.setup();
  render(<Row configured={false} />);
  expect(screen.getByText("Not configured")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "Add" }));
  await user.type(screen.getByLabelText("API key"), "first-secret");
  expect(screen.getByTestId("draft").textContent).toBe("first-secret");
});
