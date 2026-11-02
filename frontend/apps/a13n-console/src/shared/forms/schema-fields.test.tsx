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

const mountsSchema = {
  type: "object",
  properties: {
    mounts: {
      type: "array",
      title: "Host directory mounts",
      default: [],
      items: { $ref: "#/$defs/Mount" },
    },
  },
  $defs: {
    Mount: {
      type: "object",
      required: ["source", "target"],
      properties: {
        source: { type: "string", title: "Source", minLength: 1 },
        target: { type: "string", title: "Target", minLength: 1 },
        read_only: { type: "boolean", title: "Read only", default: true },
      },
    },
  },
};

it("edits referenced object arrays with required fields and boolean defaults", async () => {
  let draft: Record<string, unknown> = {};
  function Editor() {
    const [value, setValue] = useState<Record<string, unknown>>({});
    draft = value;
    return (
      <SchemaFields schema={mountsSchema} value={value} onChange={setValue} />
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  await user.click(screen.getByRole("button", { name: "Add item" }));
  expect((screen.getByLabelText("Source") as HTMLInputElement).required).toBe(
    true,
  );
  expect(
    screen
      .getByRole("switch", { name: "Read only" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(() =>
    validateSettings(mountsSchema, withSchemaValues(mountsSchema, draft)),
  ).toThrow();
  await user.type(screen.getByLabelText("Source"), "/srv/data");
  await user.type(screen.getByLabelText("Target"), "/data");
  await user.click(screen.getByRole("switch", { name: "Read only" }));
  expect(withSchemaValues(mountsSchema, draft)).toEqual({
    mounts: [{ source: "/srv/data", target: "/data", read_only: false }],
  });
  expect(() =>
    validateSettings(mountsSchema, withSchemaValues(mountsSchema, draft)),
  ).not.toThrow();
  await user.click(screen.getByRole("button", { name: "Add item" }));
  await user.type(screen.getAllByLabelText("Source")[1], "/srv/other");
  const retained = screen.getAllByLabelText("Source")[1];
  await user.click(
    screen.getAllByRole("button", { name: "Remove item {{number}}" })[0],
  );
  expect(screen.getByLabelText("Source")).toBe(retained);
  expect((screen.getByLabelText("Source") as HTMLInputElement).value).toBe(
    "/srv/other",
  );
});

it("applies defaults inside arrays without losing explicit false or unknown fields", () => {
  expect(
    withSchemaValues(mountsSchema, {
      mounts: [
        { source: "/srv/a", target: "/a", extension: "kept" },
        { source: "/srv/b", target: "/b", read_only: false },
      ],
    }),
  ).toEqual({
    mounts: [
      { source: "/srv/a", target: "/a", read_only: true, extension: "kept" },
      { source: "/srv/b", target: "/b", read_only: false },
    ],
  });
});

it("edits scalar arrays, respects bounds, and reloads external values", async () => {
  const portsSchema = {
    properties: {
      ports: {
        type: "array",
        minItems: 1,
        maxItems: 2,
        uniqueItems: true,
        items: { type: "integer", minimum: 1, maximum: 65535 },
      },
    },
  };
  let draft: Record<string, unknown> = {};
  function Editor() {
    const [value, setValue] = useState<Record<string, unknown>>({
      ports: [8080],
    });
    draft = value;
    return (
      <>
        <SchemaFields schema={portsSchema} value={value} onChange={setValue} />
        <button onClick={() => setValue({ ports: [443] })}>Reload</button>
      </>
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  expect(
    (
      screen.getByRole("button", {
        name: "Remove item {{number}}",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Add item" }));
  expect(
    (screen.getByRole("button", { name: "Add item" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(() => validateSettings(portsSchema, draft)).toThrow();
  await user.type(screen.getAllByRole("spinbutton")[1], "8081");
  expect(draft).toEqual({ ports: [8080, 8081] });
  expect(() => validateSettings(portsSchema, draft)).not.toThrow();
  await user.click(screen.getByRole("button", { name: "Reload" }));
  expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe(
    "443",
  );
});

it("inherits disabled form state and retains JSON for arrays without item fields", () => {
  render(
    <fieldset disabled>
      <SchemaFields
        schema={mountsSchema}
        value={{ mounts: [{ source: "/srv/data", target: "/data" }] }}
        onChange={vi.fn()}
      />
    </fieldset>,
  );
  expect(screen.getByLabelText("Source").matches(":disabled")).toBe(true);
  expect(
    screen.getByRole("button", { name: "Add item" }).matches(":disabled"),
  ).toBe(true);
  cleanup();
  render(
    <SchemaFields
      schema={{
        properties: { values: { type: "array", items: { type: "object" } } },
      }}
      value={{ values: [{ arbitrary: true }] }}
      onChange={vi.fn()}
    />,
  );
  expect(
    (screen.getByRole("textbox", { name: "Values" }) as HTMLTextAreaElement)
      .value,
  ).toContain('"arbitrary": true');
});

it("preserves valid empty strings when editing scalar array items", async () => {
  const stringsSchema = {
    properties: { values: { type: "array", items: { type: "string" } } },
  };
  let draft: Record<string, unknown> = {};
  function Editor() {
    const [value, setValue] = useState<Record<string, unknown>>({
      values: [""],
    });
    draft = value;
    return (
      <SchemaFields schema={stringsSchema} value={value} onChange={setValue} />
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  const input = screen.getByRole("textbox") as HTMLInputElement;
  expect(input.checkValidity()).toBe(true);
  await user.type(input, "temporary");
  await user.clear(input);
  expect(draft).toEqual({ values: [""] });
  expect(input.checkValidity()).toBe(true);
  expect(() => validateSettings(stringsSchema, draft)).not.toThrow();
});

it("retains JSON editing for nullable array items", () => {
  render(
    <SchemaFields
      schema={{
        properties: {
          values: { type: "array", items: { $ref: "#/$defs/Nullable" } },
        },
        $defs: { Nullable: { anyOf: [{ type: "string" }, { type: "null" }] } },
      }}
      value={{ values: [null, ""] }}
      onChange={vi.fn()}
    />,
  );
  const input = screen.getByRole("textbox", {
    name: "Values",
  }) as HTMLTextAreaElement;
  expect(JSON.parse(input.value)).toEqual([null, ""]);
  expect(screen.queryByRole("button", { name: "Add item" })).toBeNull();
});
