import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import { CodeBlock, JsonView, TextAreaField } from "../../shared/forms";
import { OptionsRefresh } from "../../shared/forms/options-refresh";
import { credentialQueries, type Credential } from "./api";
import { SessionPolicyJson } from "./session-policy-json";
import {
  credentialExample,
  sessionPolicyError,
  type SecretReference,
  type SessionPolicy,
} from "./session-policy";
import shared from "../../shared/shared.module.css";
import styles from "./credentials.module.css";

const modes = [
  { value: "default", label: "Environment default" },
  { value: "inherit", label: "Inherit environment network" },
  { value: "deny", label: "Deny outbound network" },
  { value: "controlled", label: "Controlled HTTPS egress" },
] as const;

/** Edits only network policy; all other Session settings survive unchanged. */
export function SessionPolicyFields({
  value,
  onChange,
  readOnly = false,
}: {
  value: SessionPolicy;
  onChange: (value: SessionPolicy) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const secrets = value.egress?.secrets ?? [];
  const { egress: _egress, egress_mode: _mode, ...other } = value;
  const error = sessionPolicyError(value);
  return (
    <div className={shared.stack}>
      <ChoiceField
        label={t("Outbound network")}
        value={value.egress_mode ?? "default"}
        readOnly={readOnly}
        options={modes.map((mode) => ({
          ...mode,
          label: t(mode.label),
          disabled: !!secrets.length && mode.value !== "controlled",
        }))}
        onValueChange={(selected) => {
          const mode = modes.find((item) => item.value === selected)?.value;
          if (!mode) return;
          onChange({
            ...value,
            egress_mode: mode === "default" ? null : mode,
            egress:
              mode === "controlled"
                ? (value.egress ?? {
                    destinations: { mode: "allowlist", hosts: [] },
                    secrets: [],
                  })
                : null,
          });
        }}
      />
      {!!secrets.length && !readOnly && (
        <p className={styles.note}>
          {t(
            "Remove credential bindings before changing to another network mode.",
          )}
        </p>
      )}
      {value.egress && (
        <>
          <ChoiceField
            label={t("Destinations")}
            readOnly={readOnly}
            value={value.egress.destinations.mode}
            options={[
              { value: "allowlist", label: t("Only listed destinations") },
              { value: "public", label: t("Public destinations") },
            ]}
            onValueChange={(mode) => {
              if (
                !value.egress ||
                mode === value.egress.destinations.mode ||
                (mode !== "allowlist" && mode !== "public")
              )
                return;
              onChange({
                ...value,
                egress: {
                  ...value.egress,
                  destinations:
                    mode === "public"
                      ? { mode }
                      : {
                          mode,
                          hosts: [
                            ...new Set(
                              secrets.flatMap((item) => item.inject_hosts),
                            ),
                          ],
                        },
                },
              });
            }}
          />
          {value.egress.destinations.mode === "allowlist" && (
            <TextAreaField
              label={t("Allowed destinations")}
              value={(value.egress.destinations.hosts ?? []).join("\n")}
              readOnly={readOnly}
              rows={3}
              hint={t(
                "Exact DNS names or public IP addresses, separated by commas or new lines. An empty list denies external access.",
              )}
              onChange={(text) => {
                if (
                  !value.egress ||
                  value.egress.destinations.mode !== "allowlist"
                )
                  return;
                onChange({
                  ...value,
                  egress: {
                    ...value.egress,
                    destinations: {
                      ...value.egress.destinations,
                      hosts: text.split("\n"),
                    },
                  },
                });
              }}
            />
          )}
          <CredentialBindings
            value={secrets}
            readOnly={readOnly}
            onChange={(next) => {
              if (value.egress)
                onChange({
                  ...value,
                  egress: { ...value.egress, secrets: next },
                });
            }}
          />
        </>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {t(error)}
        </p>
      )}
      <p className={styles.note}>
        {t(
          "Environment support is checked during preparation. Unsupported environments fail explicitly; tokens are never copied into ordinary environment variables.",
        )}
      </p>
      {Object.keys(other).length > 0 && (
        <DisclosureSection title={t("Other Session settings")}>
          <JsonView value={other} />
          <p className={styles.note}>
            {t(
              "These existing settings are preserved when you edit network access or credentials.",
            )}
          </p>
        </DisclosureSection>
      )}
      {!readOnly && <SessionPolicyJson value={value} onChange={onChange} />}
    </div>
  );
}

function CredentialBindings({
  value,
  onChange,
  readOnly,
}: {
  value: SecretReference[];
  onChange: (value: SecretReference[]) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace();
  const credentials = useQuery({
    ...credentialQueries(client, workspace.id).directory(),
    enabled: can("read"),
  });
  return (
    <div className={shared.stack}>
      <div className={styles.bindingHeading}>
        <h4>{t("Credential bindings")}</h4>
        <OptionsRefresh
          onRefresh={() => credentials.refetch({ throwOnError: true })}
        />
      </div>
      <ErrorNotice
        error={credentials.error}
        retry={() => void credentials.refetch()}
      />
      {!value.length && (
        <p className={styles.note}>
          {t("No credentials selected for this Session.")}
        </p>
      )}
      {value.map((reference, index) => (
        <CredentialBinding
          key={index}
          reference={reference}
          credentials={credentials.data ?? []}
          readOnly={readOnly}
          onChange={(next) =>
            onChange(value.map((item, at) => (at === index ? next : item)))
          }
          onRemove={() => onChange(value.filter((_, at) => at !== index))}
        />
      ))}
      {!readOnly && (
        <div className={styles.bindingHeading}>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={value.length >= 64}
            onClick={() =>
              onChange([
                ...value,
                { env: "", credential_id: "", inject_hosts: [] },
              ])
            }
          >
            {t("Add credential binding")}
          </Button>
          <Link to={`${basePath}/credentials`}>{t("Manage credentials")}</Link>
        </div>
      )}
      {!!value.length && <CredentialUsagePreview references={value} />}
    </div>
  );
}

function CredentialBinding({
  reference,
  credentials,
  onChange,
  onRemove,
  readOnly,
}: {
  reference: SecretReference;
  credentials: Credential[];
  onChange: (value: SecretReference) => void;
  onRemove: () => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace();
  const credential = credentials.find(
    (item) => item.id === reference.credential_id,
  );
  const hosts = [
    ...new Set([
      ...(credential?.allowed_hosts ?? []),
      ...reference.inject_hosts,
    ]),
  ];
  return (
    <div className={styles.binding}>
      <ChoiceField
        label={t("Credential")}
        value={reference.credential_id}
        readOnly={readOnly}
        options={[
          { value: "", label: t("Choose a credential") },
          ...credentials
            .filter(
              (item) =>
                item.state === "active" || item.id === reference.credential_id,
            )
            .map((item) => ({
              value: item.id,
              label: item.name,
              disabled: item.state !== "active",
            })),
          ...(reference.credential_id && !credential
            ? [
                {
                  value: reference.credential_id,
                  label: `${t("Unavailable credential")} · ${reference.credential_id}`,
                  disabled: true,
                },
              ]
            : []),
        ]}
        onValueChange={(id) => {
          const selected = credentials.find((item) => item.id === id);
          onChange({
            ...reference,
            credential_id: id,
            inject_hosts: reference.inject_hosts.filter((host) =>
              selected?.allowed_hosts.includes(host),
            ),
          });
        }}
      />
      <FormField label={t("Variable name")}>
        <Input
          value={reference.env}
          readOnly={readOnly}
          maxLength={128}
          placeholder="GH_TOKEN"
          onChange={(event) =>
            onChange({ ...reference, env: event.target.value })
          }
        />
      </FormField>
      {credential && (
        <div className={styles.bindingHeading}>
          <Link to={`${basePath}/credentials/${credential.id}`}>
            {t("Current credential version {{version}}", {
              version: credential.credential_version,
            })}
          </Link>
          <StatePill state={credential.state} />
        </div>
      )}
      {!!hosts.length && (
        <fieldset className={styles.hostChoices}>
          <legend>{t("Injection domains")}</legend>
          {hosts.map((host) => (
            <label key={host} className={styles.hostChoice}>
              <Checkbox
                checked={reference.inject_hosts.includes(host)}
                disabled={
                  readOnly ||
                  (!credential?.allowed_hosts.includes(host) &&
                    !reference.inject_hosts.includes(host))
                }
                onCheckedChange={(checked) =>
                  onChange({
                    ...reference,
                    inject_hosts: checked
                      ? [...reference.inject_hosts, host]
                      : reference.inject_hosts.filter((item) => item !== host),
                  })
                }
              />
              <span>{host}</span>
            </label>
          ))}
        </fieldset>
      )}
      {!readOnly && (
        <Button
          className={styles.inlineAction}
          type="button"
          size="sm"
          variant="ghost"
          onClick={onRemove}
        >
          {t("Remove binding")}
        </Button>
      )}
    </div>
  );
}

export function CredentialUsagePreview({
  references,
}: {
  references: SecretReference[];
}) {
  const { t } = useTranslation();
  return (
    <DisclosureSection title={t("Agent usage preview")}>
      <p className={styles.note}>
        {t(
          "When terminal commands are allowed, the agent receives variable names, injection domains and usage instructions. Availability is checked when the environment is prepared.",
        )}
      </p>
      {references.map((reference, index) => {
        const example = credentialExample(reference);
        return (
          example && (
            <div key={index} className={styles.summary}>
              <strong>{reference.env}</strong>
              <span>{reference.inject_hosts.join(", ")}</span>
              <CodeBlock>
                <code className="language-bash">{example}</code>
              </CodeBlock>
            </div>
          )
        );
      })}
      <p className={styles.note}>
        {t(
          "Use the authentication scheme required by the service. Commands receive placeholders; only HTTPS request headers to the selected domains receive the real token. Keep TLS verification enabled. Rotation does not change the command.",
        )}
      </p>
    </DisclosureSection>
  );
}
