import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input } from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import {
  SettingsChoice as ChoiceField,
  SettingsTextField as TextField,
  SettingsRow,
} from "../shell/settings";
import management from "./management.module.css";
import styles from "../shell/workbench.module.css";

export type CredentialSource = { env?: string; credential_ref?: string };

/** Secrets never leave this memory-only component except for an explicit key save. */
export function CredentialSourceField({
  suggestedEnv = "PROVIDER_API_KEY",
  label = "API key",
  generic = false,
  value,
  onChange,
}: {
  suggestedEnv?: string;
  label?: string;
  generic?: boolean;
  value: CredentialSource | undefined;
  onChange: (value: CredentialSource | undefined) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const [secret, setSecret] = useState("");
  const [expanded, setExpanded] = useState(
    !value || !(value.env || value.credential_ref),
  );
  // Allocate once: a lost acknowledgement is reconciled against this same reference.
  const [reference, setReference] = useState(
    () => `key-${crypto.randomUUID().replaceAll("-", "")}`,
  );
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const latestAuth = useRef(JSON.stringify(value));
  latestAuth.current = JSON.stringify(value);
  const save = useMutation({
    mutationFn: async () => {
      const attempt = { reference, authentication: JSON.stringify(value) };
      await result(
        client.PUT("/api/auth/keys", {
          body: { credential_ref: reference, key: secret },
        }),
      );
      return attempt;
    },
    onSuccess: (attempt) => {
      void queries.invalidateQueries({ queryKey: ["keys"] });
      if (!active.current || attempt.authentication !== latestAuth.current)
        return;
      setSecret("");
      setExpanded(false);
      onChange({ credential_ref: attempt.reference });
      setReference(`key-${crypto.randomUUID().replaceAll("-", "")}`);
    },
    onError: () => {
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const selected = value?.env != null ? "env" : (value?.credential_ref ?? "");
  const choices = [
    {
      value: "",
      label: t(generic ? "Save a new credential" : "Save a new API key"),
    },
    ...(keys.data ?? []).map((key) => ({
      value: key.credential_ref,
      label: key.credential_ref,
    })),
    { value: "env", label: t("Server environment variable") },
  ];
  if (selected && !choices.some((item) => item.value === selected))
    choices.push({
      value: selected,
      label: t("{{reference}} (saved reference)", { reference: selected }),
    });
  return (
    <fieldset
      aria-label={t(label)}
      disabled={save.isPending}
      className={styles.stack}
    >
      {!expanded ? (
        <SettingsRow
          label={t(label)}
          description={
            selected === "env"
              ? t("Environment - {{name}}", { name: value?.env })
              : keys.data?.some((key) => key.credential_ref === selected)
                ? t(generic ? "Stored credential" : "Stored API key")
                : t(generic ? "Credential reference" : "API key reference")
          }
        >
          <Button variant="ghost" onClick={() => setExpanded(true)}>
            {t("Change credential")}
          </Button>
        </SettingsRow>
      ) : (
        <>
          <ChoiceField
            label={t("Credential source")}
            value={selected}
            options={choices}
            onValueChange={(key) => {
              setSecret("");
              onChange(
                key === "env"
                  ? {
                      env: suggestedEnv,
                    }
                  : key
                    ? { credential_ref: key }
                    : undefined,
              );
            }}
          />
          {selected === "env" ? (
            <TextField
              label={t("Environment variable")}
              value={value?.env ?? ""}
              onChange={(env) => onChange({ env })}
              description={t("Read from the server environment.")}
            />
          ) : (
            !selected && (
              <>
                <SettingsRow
                  label={t(generic ? label : "Provider API key")}
                  description={t(
                    generic
                      ? "Shared by this server. Saved separately from the account."
                      : "Shared by this server. Saved separately from the model.",
                  )}
                >
                  <Input
                    aria-label={t(generic ? label : "Provider API key")}
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    onChange={(event) => setSecret(event.target.value)}
                  />
                </SettingsRow>
                <div className={management.groupActions}>
                  <Button
                    variant="outline"
                    loading={save.isPending}
                    disabled={!secret.trim()}
                    onClick={() => save.mutate()}
                  >
                    {t(generic ? "Save credential" : "Save API key")}
                  </Button>
                  {keys.data?.some(
                    (key) => key.credential_ref === reference,
                  ) && (
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSecret("");
                        onChange({
                          credential_ref: reference,
                        });
                        setReference(
                          `key-${crypto.randomUUID().replaceAll("-", "")}`,
                        );
                      }}
                    >
                      {t("Use saved key")}
                    </Button>
                  )}
                </div>
              </>
            )
          )}
        </>
      )}
      <ErrorNotice error={save.error || keys.error} />
    </fieldset>
  );
}
