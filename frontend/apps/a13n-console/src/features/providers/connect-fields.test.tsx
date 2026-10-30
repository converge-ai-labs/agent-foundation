// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { ProviderConnectFields } from "./connect-fields";
import { withSchemaValues } from "../../shared/forms";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);
const schema = {
  properties: {
    docker_host: { type: "string" },
    envd_http: {
      title: "Envd HTTP connection",
      default: null,
      anyOf: [{ $ref: "#/$defs/Http" }, { type: "null" }],
    },
  },
  $defs: {
    Http: {
      type: "object",
      required: ["host"],
      properties: {
        host: { type: "string", title: "Host access address" },
        bind_host: { type: "string", title: "Listen address", default: null },
        credential_file: {
          type: "string",
          title: "Credential file",
          default: "/var/lib/a13n-envd/http-token",
        },
      },
    },
  },
};
function Form() {
  const [value, setValue] = useState<Record<string, unknown>>({
    docker_host: "tcp://engine:2376",
  });
  return (
    <>
      <ProviderConnectFields
        configurationSchema={schema}
        configuration={value}
        onConfigurationChange={setValue}
        credential={{}}
        onCredentialChange={() => {}}
        name="Docker"
        onNameChange={() => {}}
      />
      <output data-testid="saved">
        {JSON.stringify(withSchemaValues(schema, value))}
      </output>
    </>
  );
}
it("keeps native connections valid and configures HTTP routing independently", async () => {
  const user = userEvent.setup();
  render(<Form />);
  expect(screen.queryByLabelText("Host access address")).toBeNull();
  expect(JSON.parse(screen.getByTestId("saved").textContent!)).toEqual({
    docker_host: "tcp://engine:2376",
    envd_http: null,
  });
  await user.click(
    screen.getByRole("switch", { name: "Environment operation channel" }),
  );
  expect(screen.queryByLabelText("Listen address")).toBeNull();
  expect(screen.queryByLabelText("Credential file")).toBeNull();
  await user.type(screen.getByLabelText("Host access address"), "10.0.0.8");
  expect(JSON.parse(screen.getByTestId("saved").textContent!)).toEqual({
    docker_host: "tcp://engine:2376",
    envd_http: {
      host: "10.0.0.8",
      bind_host: null,
      credential_file: "/var/lib/a13n-envd/http-token",
    },
  });
  await user.click(
    screen.getByRole("switch", { name: "Environment operation channel" }),
  );
  expect(screen.queryByLabelText("Host access address")).toBeNull();
  expect(JSON.parse(screen.getByTestId("saved").textContent!).docker_host).toBe(
    "tcp://engine:2376",
  );
});

it("preserves explicit advanced overrides when changing the host and restores automatic listening when cleared", async () => {
  const user = userEvent.setup();
  render(<Form />);
  await user.click(
    screen.getByRole("switch", { name: "Environment operation channel" }),
  );
  await user.type(
    screen.getByLabelText("Host access address"),
    "desktop.internal",
  );
  await user.click(screen.getByRole("button", { name: "Advanced settings" }));
  await user.type(screen.getByLabelText("Listen address"), "10.0.0.8");
  const credential = screen.getByLabelText("Credential file");
  await user.clear(credential);
  await user.type(credential, "/protected/token");
  await user.clear(screen.getByLabelText("Host access address"));
  await user.type(screen.getByLabelText("Host access address"), "10.0.0.9");
  expect(
    JSON.parse(screen.getByTestId("saved").textContent!).envd_http,
  ).toEqual({
    host: "10.0.0.9",
    bind_host: "10.0.0.8",
    credential_file: "/protected/token",
  });
  await user.clear(screen.getByLabelText("Listen address"));
  expect(
    JSON.parse(screen.getByTestId("saved").textContent!).envd_http.bind_host,
  ).toBeNull();
});
