import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { credentialHint } from "../../features/providers/credential-hint";
import { SchemaFields, withSchemaValues } from "./schema-fields";
import { validateSettings } from "./validation";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

const schema = {
  type: "object",
  anyOf: [
    { $ref: "#/$defs/key", title: "API key" },
    { $ref: "#/$defs/account", title: "Service account" },
  ],
  $defs: {
    key: {
      type: "object",
      additionalProperties: false,
      required: ["api_key"],
      properties: {
        api_key: { type: "string", title: "API key", minLength: 1 },
      },
    },
    account: {
      type: "object",
      additionalProperties: false,
      required: ["private_key"],
      properties: {
        type: {
          type: "string",
          const: "service_account",
          default: "service_account",
        },
        private_key: { type: "string", title: "Private key", minLength: 1 },
      },
    },
  },
};

it("switches credential methods without retaining fields or secrets from the previous method", async () => {
  let submitted: Record<string, unknown> = {};
  function Editor() {
    const [value, setValue] = useState<Record<string, unknown>>({});
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submitted = withSchemaValues(schema, value);
          validateSettings(schema, submitted);
        }}
      >
        <SchemaFields
          schema={schema}
          value={value}
          onChange={setValue}
          secret
        />
        <button type="submit">Save</button>
      </form>
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  expect(credentialHint(schema)).toBe("Credentials");
  await user.type(screen.getByLabelText("API key"), "key-secret");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submitted).toEqual({ api_key: "key-secret" });
  await user.click(
    screen.getByRole("combobox", { name: "Authentication method" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Service account" }),
  );
  expect(screen.queryByLabelText("API key")).toBeNull();
  await user.type(screen.getByLabelText("Private key"), "private-secret");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(submitted).toEqual({
    type: "service_account",
    private_key: "private-secret",
  });
  await user.click(
    screen.getByRole("combobox", { name: "Authentication method" }),
  );
  await user.click(await screen.findByRole("option", { name: "API key" }));
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("");
  expect(screen.queryByLabelText("Private key")).toBeNull();
});

it("keeps legacy service-account drafts and applies defaults from only the selected alternative", () => {
  expect(withSchemaValues(schema, { private_key: "private-secret" })).toEqual({
    type: "service_account",
    private_key: "private-secret",
  });
  expect(withSchemaValues(schema, { api_key: "key-secret" })).toEqual({
    api_key: "key-secret",
  });
  expect(() =>
    validateSettings(schema, { api_key: "key", private_key: "private" }),
  ).toThrow();
});
