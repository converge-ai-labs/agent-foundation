import { useState } from "react";
import type { Schema } from "./api";
import { credentialMode, providerSchema } from "./provider-authentication";
import { withSchemaValues } from "./forms/schema-fields";

type CredentialDefinition = {
  authentication: Schema["Authentication"];
  configuration_schema: Record<string, unknown>;
  credential_schema?: { [key: string]: unknown } | null;
};

export type CredentialSection = {
  /** The mode the selected definition and the current configuration imply. */
  mode: Schema["CredentialMode"];
  schema: Record<string, unknown>;
  credential: Record<string, unknown>;
  setCredential: (value: Record<string, unknown>) => void;
  removing: boolean;
  setRemoving: (value: boolean) => void;
  /** A saved credential stays visible and removable after a change forbids it. */
  visible: boolean;
  removable: boolean;
  requireFields: boolean;
  /** The credential to send: values to store, null to remove, undefined to leave alone. */
  payload: () => Record<string, unknown> | null | undefined;
};

/** One credential state machine for every Provider editor. */
export function useCredentialSection(
  definition: CredentialDefinition | undefined,
  configuration: Record<string, unknown>,
  saved?: { credential_configured?: boolean } | null,
): CredentialSection {
  const [credential, setCredential] = useState<Record<string, unknown>>({});
  const [removing, setRemoving] = useState(false);
  const mode = credentialMode(definition, configuration);
  const schema = providerSchema(definition?.credential_schema);
  const configured = saved?.credential_configured ?? false;
  const replacing =
    mode !== "forbidden" &&
    (Object.keys(credential).length > 0 ||
      (mode === "required" && !configured));
  return {
    mode,
    schema,
    credential,
    setCredential,
    removing,
    setRemoving: (value) => {
      setRemoving(value);
      setCredential({});
    },
    visible: mode !== "forbidden" || configured,
    removable: configured,
    requireFields: replacing && !removing,
    payload: () => {
      // An unavailable definition reports "forbidden"; that must not delete a saved credential.
      if (removing || (!!definition && mode === "forbidden" && configured))
        return null;
      return replacing ? withSchemaValues(schema, credential) : undefined;
    },
  };
}
