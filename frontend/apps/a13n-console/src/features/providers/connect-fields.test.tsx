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
        host: { type: "string", title: "Reachable host" },
        bind_host: { type: "string", default: "127.0.0.1" },
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
  expect(screen.queryByLabelText("Reachable host")).toBeNull();
  expect(JSON.parse(screen.getByTestId("saved").textContent!)).toEqual({
    docker_host: "tcp://engine:2376",
    envd_http: null,
  });
  await user.click(
    screen.getByRole("switch", { name: "Envd HTTP connection" }),
  );
  await user.type(screen.getByLabelText("Reachable host"), "10.0.0.8");
  expect(JSON.parse(screen.getByTestId("saved").textContent!)).toEqual({
    docker_host: "tcp://engine:2376",
    envd_http: { host: "10.0.0.8", bind_host: "127.0.0.1" },
  });
  await user.click(
    screen.getByRole("switch", { name: "Envd HTTP connection" }),
  );
  expect(screen.queryByLabelText("Reachable host")).toBeNull();
  expect(JSON.parse(screen.getByTestId("saved").textContent!).docker_host).toBe(
    "tcp://engine:2376",
  );
});
