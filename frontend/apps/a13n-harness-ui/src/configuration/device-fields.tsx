import { ChoiceField, SettingsSection } from "a13n-ui";
import { TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";

export function DeviceFields({
  source,
  onChange,
}: {
  source: string;
  onChange: (source: string) => void;
}) {
  const document = readDocument(source)!;
  const text = (path: string[]) => String(document.getIn(path) ?? "");
  const set = (path: string[], value: unknown) =>
    onChange(updateDocument(source, path, value));
  const carrier = text(["transport", "kind"]);
  const storedKey = document.hasIn(["authentication", "credential_ref"]);
  return (
    <SettingsSection
      title="Device connection"
      description="Save the connection once; select working directories separately for Projects and conversations."
    >
      <TextField
        label="Native Device ID"
        value={text(["device_id"])}
        onChange={(value) => set(["device_id"], value)}
        description="The device_id configured in envd, not the Host resource ID above."
      />
      <ChoiceField
        label="Transport"
        value={carrier}
        onValueChange={(kind) =>
          set(["transport"], {
            kind,
            configuration: kind === "http" ? { endpoint: "" } : {},
          })
        }
        options={[
          { value: "http", label: "HTTP · connect to envd" },
          {
            value: "websocket",
            label: "WebSocket · envd connects to this server",
          },
        ]}
      />
      {carrier === "http" ? (
        <TextField
          label="Device endpoint"
          value={text(["transport", "configuration", "endpoint"])}
          onChange={(value) =>
            set(["transport", "configuration", "endpoint"], value)
          }
          description="The envd HTTP origin, for example https://device.example.com."
        />
      ) : (
        <p>
          Configure envd to connect to this server at /api/devices/
          {text(["id"])}/connect using the referenced credential.
        </p>
      )}
      <ChoiceField
        label="Credential source"
        value={storedKey ? "stored" : "env"}
        onValueChange={(value) =>
          set(
            ["authentication"],
            value === "stored"
              ? { kind: "api_key", credential_ref: "" }
              : { kind: "api_key", env: "A13N_DEVICE_TOKEN" },
          )
        }
        options={[
          { value: "env", label: "Server environment variable" },
          { value: "stored", label: "Saved API key reference" },
        ]}
      />
      <TextField
        label={
          storedKey ? "Saved credential reference" : "Environment variable"
        }
        value={text(["authentication", storedKey ? "credential_ref" : "env"])}
        onChange={(value) =>
          set(["authentication"], {
            kind: "api_key",
            [storedKey ? "credential_ref" : "env"]: value,
          })
        }
        description="Enter a reference, never the credential itself. Saved keys are managed in Accounts & API keys."
      />
    </SettingsSection>
  );
}
