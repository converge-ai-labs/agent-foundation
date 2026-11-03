import { useTranslation } from "../i18n";
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
  const { t } = useTranslation();
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
        title={t("Paired Device")}
        description={t(
          "This device is approved. Its credential is saved on the device; you don't need to enter it here.",
        )}
      >
        <SettingsRow label={t("Device ID")}>
          <span>{text(["device_id"])}</span>
        </SettingsRow>
        <SettingsRow
          label={t("Connection")}
          description={
            document.getIn(["authentication", "revoked"])
              ? t("This credential can no longer connect.")
              : t("Check or revoke this connection in Environments.")
          }
        >
          <span>
            {document.getIn(["authentication", "revoked"])
              ? t("Revoked")
              : t("Registered")}
          </span>
        </SettingsRow>
      </SettingsSection>
    );
  }
  return (
    <SettingsSection
      variant="grouped"
      title={t("Device connection")}
      description={t(
        "Save the connection here. Choose folders in each project or chat.",
      )}
    >
      <TextField
        label={t("Device ID")}
        value={text(["device_id"])}
        onChange={(value) => set(["device_id"], value)}
        description={t(
          "The device_id set in envd, separate from the configuration ID above.",
        )}
      />
      <ChoiceField
        label={t("Transport")}
        value={carrier}
        onValueChange={(kind) =>
          set(["transport"], {
            kind,
            configuration: kind === "http" ? { endpoint: "" } : {},
          })
        }
        options={[
          { value: "http", label: t("HTTP · connect to envd") },
          {
            value: "websocket",
            label: t("WebSocket · envd connects to this server"),
          },
        ]}
      />
      {carrier === "http" ? (
        <TextField
          label={t("Device endpoint")}
          value={text(["transport", "configuration", "endpoint"])}
          onChange={(value) =>
            set(["transport", "configuration", "endpoint"], value)
          }
          description={t(
            "The envd HTTP origin, for example https://device.example.com.",
          )}
        />
      ) : (
        <SettingsRow
          label={t("Server connection")}
          description={t(
            "Configure envd with this path and the referenced credential.",
          )}
        >
          <span>/api/devices/{text(["id"])}/connect</span>
        </SettingsRow>
      )}
      <ChoiceField
        label={t("Credential source")}
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
          { value: "env", label: t("Server environment variable") },
          { value: "stored", label: t("Saved API key reference") },
        ]}
      />
      <TextField
        label={
          storedKey
            ? t("Saved credential reference")
            : t("Environment variable")
        }
        value={text(["authentication", storedKey ? "credential_ref" : "env"])}
        onChange={(value) =>
          set(["authentication"], {
            kind: "api_key",
            [storedKey ? "credential_ref" : "env"]: value,
          })
        }
        description={t(
          "Enter a key reference, not the key itself. Manage saved keys in Accounts & API keys.",
        )}
      />
    </SettingsSection>
  );
}
