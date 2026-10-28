import { SettingsTextField as TextField } from "../shell/settings";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import { SettingsRow, SettingsSection } from "../shell/settings";

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
  if (text(["authentication", "kind"]) === "paired") {
    return (
      <SettingsSection
        variant="grouped"
        title="Paired Device"
        description="This device is approved. Its credential is saved on the device; you don't need to enter it here."
      >
        <SettingsRow label="Device ID">
          <span>{text(["device_id"])}</span>
        </SettingsRow>
        <SettingsRow
          label="Connection"
          description={
            document.getIn(["authentication", "revoked"])
              ? "This credential can no longer connect."
              : "Check or revoke this connection in Environments."
          }
        >
          <span>
            {document.getIn(["authentication", "revoked"])
              ? "Revoked"
              : "Registered"}
          </span>
        </SettingsRow>
      </SettingsSection>
    );
  }
  return (
    <SettingsSection
      variant="grouped"
      title="Device connection"
      description="Save the connection here. Choose folders in each project or chat."
    >
      <TextField
        label="Device ID"
        value={text(["device_id"])}
        onChange={(value) => set(["device_id"], value)}
        description="The device_id set in envd, separate from the configuration ID above."
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
        <SettingsRow
          label="Server connection"
          description="Configure envd with this path and the referenced credential."
        >
          <span>/api/devices/{text(["id"])}/connect</span>
        </SettingsRow>
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
        description="Enter a key reference, not the key itself. Manage saved keys in Accounts & API keys."
      />
    </SettingsSection>
  );
}
