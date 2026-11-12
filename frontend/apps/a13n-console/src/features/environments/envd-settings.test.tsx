import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { EnvdSettings } from "./envd-settings";
import { ProviderConfiguration } from "./provider-configuration";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name: string) =>
        String(values?.[name] ?? name),
      ),
  }),
}));
const limits = {
  type: "object",
  additionalProperties: false,
  properties: {
    max_sessions: { type: "integer", default: 128, "x-envd-group": "capacity" },
    max_concurrent_file_transfers: {
      type: "integer",
      default: 64,
      "x-envd-group": "capacity",
    },
    max_device_file_transfers: {
      type: "integer",
      default: 128,
      "x-envd-group": "capacity",
    },
    max_processes: {
      type: "integer",
      default: 128,
      "x-envd-group": "capacity",
    },
    max_device_processes: {
      type: "integer",
      default: 512,
      "x-envd-group": "capacity",
    },
    max_transfer_frame_bytes: {
      type: "integer",
      default: 4194304,
      minimum: 26,
      "x-envd-group": "protocol",
      "x-envd-unit": "bytes",
    },
  },
};
const schema = {
  properties: { envd: { $ref: "#/$defs/Settings" } },
  $defs: {
    Settings: {
      type: "object",
      properties: {
        limits: { $ref: "#/$defs/Limits" },
        idle_timeout_ms: {
          type: "integer",
          default: 3600000,
          "x-envd-group": "lifecycle",
          "x-envd-unit": "milliseconds",
        },
      },
    },
    Limits: limits,
  },
};

it("keeps unrelated recipe fields, converts durations, and removes cleared overrides", async () => {
  const user = userEvent.setup();
  function Editor() {
    const [recipe, setRecipe] = useState<Record<string, unknown>>({
      image: "custom",
      environment: { APP: "kept" },
    });
    return (
      <>
        <EnvdSettings schema={schema} recipe={recipe} onChange={setRecipe} />
        <pre data-testid="recipe">{JSON.stringify(recipe)}</pre>
      </>
    );
  }
  render(<Editor />);
  await user.click(screen.getByRole("button", { name: "Capacity" }));
  expect(JSON.parse(screen.getByTestId("recipe").textContent!)).toEqual({
    image: "custom",
    environment: { APP: "kept" },
  });
  const transfers = screen.getByRole("spinbutton", {
    name: "Max concurrent file transfers",
  });
  await user.type(transfers, "32");
  await user.click(
    screen.getByRole("button", { name: "Session lifetime defaults" }),
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Idle timeout" }),
    "1.001",
  );
  expect(JSON.parse(screen.getByTestId("recipe").textContent!)).toEqual({
    image: "custom",
    environment: { APP: "kept" },
    envd: {
      limits: { max_concurrent_file_transfers: 32 },
      idle_timeout_ms: 1001,
    },
  });
  await user.clear(transfers);
  await user.clear(screen.getByRole("spinbutton", { name: "Idle timeout" }));
  expect(JSON.parse(screen.getByTestId("recipe").textContent!)).toEqual({
    image: "custom",
    environment: { APP: "kept" },
  });
});

it("enforces the protocol frame minimum from the schema", async () => {
  const user = userEvent.setup();
  function Editor() {
    const [recipe, onChange] = useState<Record<string, unknown>>({});
    return <EnvdSettings schema={schema} recipe={recipe} onChange={onChange} />;
  }
  render(<Editor />);
  await user.click(screen.getByRole("button", { name: "Protocol sizing" }));
  const frame = screen.getByRole("spinbutton", {
    name: "Max transfer frame bytes",
  }) as HTMLInputElement;
  fireEvent.change(frame, { target: { value: "25" } });
  expect(frame.checkValidity()).toBe(false);
  fireEvent.change(frame, { target: { value: "26" } });
  expect(frame.checkValidity()).toBe(true);
});

it("shows Envd settings for native execution and keeps read-only controls disabled", async () => {
  const user = userEvent.setup();
  render(
    <ProviderConfiguration
      schema={schema}
      text='{"execution":"native"}'
      onChange={vi.fn()}
      readOnly
    />,
  );

  await user.click(screen.getByRole("button", { name: "Capacity" }));
  expect(
    screen
      .getByRole("spinbutton", { name: "Max sessions" })
      .matches(":disabled"),
  ).toBe(true);
});

it("edits each setting under its owner without a second legacy Envd input", async () => {
  const user = userEvent.setup();
  const groupedSchema = {
    ...schema,
    "x-primary-fields": ["image", "timeout_seconds", "execution"],
    properties: {
      ...schema.properties,
      image: { type: "string", title: "Image" },
      timeout_seconds: { type: "integer", default: 3600 },
      execution: {
        type: "string",
        title: "Execution connection",
        enum: ["native", "envd_http"],
      },
      request_timeout_seconds: { type: "integer", default: 60 },
      max_file_bytes: {
        type: "integer",
        title: "Legacy file limit",
        default: 1024,
      },
      environment: { type: "object", additionalProperties: { type: "string" } },
    },
  };
  const change = vi.fn();
  render(
    <ProviderConfiguration
      variant="docker"
      schema={groupedSchema}
      text='{"image":"custom","execution":"envd_http","max_file_bytes":1024}'
      onChange={change}
    />,
  );
  const platform = screen.getByRole("region", {
    name: "Provider platform settings",
  });
  await user.click(screen.getByRole("button", { name: "Capacity" }));
  const daemon = screen.getByRole("region", { name: "Envd settings" });
  const features = screen.getByRole("region", { name: "Runtime settings" });
  expect(
    platform.contains(screen.getByRole("textbox", { name: "Container image" })),
  ).toBe(true);
  expect(
    platform.contains(
      screen.getByRole("spinbutton", { name: "Timeout seconds" }),
    ),
  ).toBe(true);
  expect(
    daemon.contains(screen.getByRole("spinbutton", { name: "Max sessions" })),
  ).toBe(true);
  expect(
    features.contains(
      screen.getByRole("combobox", { name: "Execution connection" }),
    ),
  ).toBe(true);
  expect(
    screen.queryByRole("spinbutton", { name: "Legacy file limit" }),
  ).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Advanced environment configuration" }),
  );
  expect(
    features.contains(
      screen.getByRole("spinbutton", {
        name: "Request timeout seconds",
      }),
    ),
  ).toBe(true);
  await user.type(
    screen.getByRole("spinbutton", { name: "Max sessions" }),
    "8",
  );
  expect(JSON.parse(change.mock.lastCall![0])).toMatchObject({
    image: "custom",
    execution: "envd_http",
    max_file_bytes: 1024,
    envd: { limits: { max_sessions: 8 } },
  });
});

it("switches the whole recipe between grouped fields and one JSON editor", async () => {
  const user = userEvent.setup();
  const initial = {
    image: "kept",
    execution: "envd_http",
    envd: { limits: { max_sessions: 8 } },
  };
  function Editor() {
    const [text, change] = useState(JSON.stringify(initial));
    return (
      <ProviderConfiguration schema={schema} text={text} onChange={change} />
    );
  }
  render(<Editor />);
  const panel = screen.getByRole("region", { name: "Configuration" });
  expect(
    panel.contains(screen.getByRole("region", { name: "Envd settings" })),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "JSON" }));
  const json = screen.getByRole("textbox", {
    name: "Template configuration (JSON)",
  }) as HTMLTextAreaElement;
  expect(JSON.parse(json.value)).toEqual(initial);
  expect(
    screen.queryByRole("region", { name: "Provider platform settings" }),
  ).toBeNull();
  expect(screen.queryByRole("region", { name: "Runtime settings" })).toBeNull();
  fireEvent.change(json, {
    target: {
      value: JSON.stringify({
        ...initial,
        envd: { limits: { max_sessions: 16 } },
      }),
    },
  });
  await user.click(screen.getByRole("button", { name: "Fields" }));
  await user.click(screen.getByRole("button", { name: "Capacity" }));
  const sessions = screen.getByRole("spinbutton", {
    name: "Max sessions",
  }) as HTMLInputElement;
  expect(sessions.value).toBe("16");
  fireEvent.change(sessions, { target: { value: "12" } });
  await user.click(screen.getByRole("button", { name: "JSON" }));
  expect(
    JSON.parse(
      (
        screen.getByRole("textbox", {
          name: "Template configuration (JSON)",
        }) as HTMLTextAreaElement
      ).value,
    ),
  ).toEqual({ ...initial, envd: { limits: { max_sessions: 12 } } });
});
