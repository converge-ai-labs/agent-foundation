import {
  Button,
  ChoiceField,
  FormField,
  Input,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import { InfoIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { ProviderIcon } from "../../shared/identity";
import { providersPath } from "../providers/navigation";
import {
  eligibleWebProvider,
  type WebOperation,
  type WebProvider,
  type WebProviderDefinition,
} from "./toolset-state";
import styles from "./agents.module.css";

type Config = NonNullable<Schema["ToolSelection"]["config"]>;

export function WebToolSettings({
  operation,
  providers,
  types,
  providerError,
  config,
  onChange,
  readOnly,
  reuseProviderId,
}: {
  operation: WebOperation;
  providers: WebProvider[];
  types: WebProviderDefinition[];
  providerError: Error | null;
  config: Config;
  onChange: (config: Config) => void;
  readOnly: boolean;
  reuseProviderId?: Schema["JsonValue"];
}) {
  const { t } = useTranslation();
  const { workspace, can } = useWorkspace();
  const providerId =
    typeof config.provider_id === "string" ? config.provider_id : "";
  const selected = providers.find((provider) => provider.id === providerId);
  const options: { value: string; label: string; icon?: ReactNode }[] =
    providers
      .filter(
        (provider) =>
          provider.id === providerId ||
          eligibleWebProvider(provider, types, operation),
      )
      .map((provider) => ({
        value: provider.id,
        label: `${provider.name} · ${provider.type} · ${t(provider.workspace_id ? "Workspace" : "Organization")}`,
        icon: <ProviderIcon type={provider.type} />,
      }));
  if (providerId && !selected)
    options.push({
      value: providerId,
      label: t("Selected provider unavailable"),
    });
  const displayedProviderId = providerId || options[0]?.value;
  return (
    <>
      <div className={styles.webProviderField}>
        <ChoiceField
          readOnly={readOnly}
          label={t("Web Provider")}
          value={displayedProviderId}
          placeholder={t("Select a provider")}
          onValueChange={(id) => onChange({ ...config, provider_id: id })}
          options={options}
        />
        {can("web_provider.manage") && (
          <a
            href={providersPath("web", "workspace", workspace.key)}
            className={styles.webProviderLink}
          >
            {t("Manage")}
          </a>
        )}
        {typeof reuseProviderId === "string" &&
          reuseProviderId !== providerId &&
          providers.some(
            (provider) =>
              provider.id === reuseProviderId &&
              eligibleWebProvider(provider, types, operation),
          ) && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={readOnly}
              onClick={() =>
                onChange({ ...config, provider_id: reuseProviderId })
              }
            >
              {t("Use the {{operation}} provider", {
                operation: t(operation === "search" ? "scrape" : "search"),
              })}
            </Button>
          )}
        {providerId &&
          (!selected || !eligibleWebProvider(selected, types, operation)) && (
            <p role="alert" className="text-sm text-destructive">
              {t(
                "This provider is unavailable or does not support the selected operation.",
              )}
            </p>
          )}
        <ErrorNotice error={providerError} />
      </div>
      <FormField
        readOnly={readOnly}
        label={t(
          operation === "search" ? "Maximum results" : "Maximum content bytes",
        )}
      >
        <Input
          type="number"
          min={1}
          max={operation === "search" ? 10 : 4194304}
          value={Number(
            config[
              operation === "search" ? "max_results" : "max_content_bytes"
            ] ?? (operation === "search" ? 5 : 524288),
          )}
          onChange={(event) =>
            onChange({
              ...config,
              [operation === "search" ? "max_results" : "max_content_bytes"]:
                Number(event.target.value),
            })
          }
        />
      </FormField>
      <DomainSettings config={config} onChange={onChange} readOnly={readOnly} />
    </>
  );
}

export function WebLocalSettings({
  tool,
  config,
  onChange,
  readOnly,
}: {
  tool: "fetch" | "download";
  config: Config;
  onChange: (config: Config) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  return (
    <>
      {tool === "fetch" && (
        <FormField readOnly={readOnly} label={t("Maximum content bytes")}>
          <Input
            type="number"
            min={1}
            max={262144}
            value={Number(config.max_content_bytes ?? 262144)}
            onChange={(event) =>
              onChange({
                ...config,
                max_content_bytes: Number(event.target.value),
              })
            }
          />
        </FormField>
      )}
      <DomainSettings config={config} onChange={onChange} readOnly={readOnly} />
    </>
  );
}

function DomainSettings({
  config,
  onChange,
  readOnly,
}: {
  config: Config;
  onChange: (config: Config) => void;
  readOnly: boolean;
}) {
  return (
    <>
      {(["allow_domains", "deny_domains"] as const).map((key) => (
        <DomainField
          key={key}
          name={key}
          value={Array.isArray(config[key]) ? (config[key] as string[]) : []}
          readOnly={readOnly}
          onChange={(domains) => onChange({ ...config, [key]: domains })}
        />
      ))}
    </>
  );
}

function DomainField({
  name,
  value,
  onChange,
  readOnly,
}: {
  name: string;
  value: string[];
  onChange: (domains: string[]) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(value.join(", "));
  const label = t(
    name === "allow_domains" ? "Include domains" : "Exclude domains",
  );
  const help = t(
    name === "allow_domains"
      ? "Only listed domains and their subdomains are allowed. Leave empty for no allowlist. Enter hostnames separated by commas, without URLs or wildcards."
      : "Listed domains and their subdomains are always blocked, even if included. Enter hostnames separated by commas, without URLs or wildcards.",
  );
  return (
    <FormField
      readOnly={readOnly}
      label={label}
      labelAction={
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                className={styles.domainHelp}
                aria-label={t("{{field}} help", { field: label })}
              />
            }
          >
            <InfoIcon size={14} aria-hidden="true" />
          </TooltipTrigger>
          <TooltipPopup className={styles.domainTooltip}>{help}</TooltipPopup>
        </Tooltip>
      }
    >
      <Input
        value={draft}
        placeholder="example.com, docs.example.org"
        onChange={(event) => {
          setDraft(event.target.value);
          onChange(
            event.target.value
              .split(",")
              .map((domain) => domain.trim())
              .filter(Boolean),
          );
        }}
      />
    </FormField>
  );
}
