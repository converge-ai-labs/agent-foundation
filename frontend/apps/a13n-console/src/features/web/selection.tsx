import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField, DisclosureSection, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { webProviderApi } from "./api";
import { providersPath } from "../providers/navigation";

export type AgentSearchValue = {
  provider_id: string;
  max_results?: number;
  allow_domains?: string[];
  deny_domains?: string[];
};

export function AgentSearchSelection({
  value,
  onChange,
  readOnly = false,
}: {
  readOnly?: boolean;
  value: AgentSearchValue | null;
  onChange: (value: AgentSearchValue | null) => void;
}) {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    client = useClient();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const [domains, setDomains] = useState(
    value?.allow_domains?.join(", ") ?? "",
  );
  const query = useQuery({
    queryKey: ["web-providers", scope.kind, scope.id, "choices"],
    refetchOnWindowFocus: "always",
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        webProviderApi(client, scope).providers(signal, cursor),
      ),
  });
  const selected = query.data?.find((item) => item.id === value?.provider_id);
  function choose(provider_id: string) {
    if (provider_id === "off") setDomains("");
    onChange(
      provider_id === "off"
        ? null
        : {
            provider_id,
            max_results: value?.max_results ?? 5,
            allow_domains: value?.allow_domains ?? [],
            deny_domains: value?.deny_domains ?? [],
          },
    );
  }
  return (
    <DisclosureSection
      title={t(readOnly ? "Web search" : "Configure web search")}
      summary={
        selected?.name ?? t(value ? "Selected provider unavailable" : "Off")
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {t(
          "Choose one compatible Web Provider for search. Search can consume provider quota.",
        )}
      </p>
      <ChoiceField
        readOnly={readOnly}
        label={t("Search provider")}
        value={value?.provider_id ?? "off"}
        onValueChange={choose}
        options={[
          { value: "off", label: t("Off") },
          ...(query.data ?? [])
            .filter(
              (item) =>
                (item.enabled && item.credential_configured) ||
                item.id === value?.provider_id,
            )
            .map((item) => ({
              value: item.id,
              label: `${item.name} · ${item.type} · ${t(item.workspace_id ? "Workspace" : "Organization")}`,
            })),
          ...(value && !selected
            ? [
                {
                  value: value.provider_id,
                  label: t("Selected provider unavailable"),
                },
              ]
            : []),
        ]}
      />
      <ErrorNotice error={query.error} />
      {value &&
        !query.isPending &&
        (!selected?.enabled || !selected.credential_configured) && (
          <p role="alert">
            {t(
              "The selected provider is unavailable or disabled. Choose another provider before saving.",
            )}
          </p>
        )}
      {can("web_provider.manage") && (
        <a
          className="mt-2 mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          href={providersPath("search", "workspace", workspace.key)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("Manage Web Providers")}
          <ArrowSquareOutIcon size={13} aria-hidden="true" />
        </a>
      )}
      {value && (
        <>
          <FormField readOnly={readOnly} label={t("Maximum results")}>
            <Input
              type="number"
              required
              min={1}
              max={10}
              value={value.max_results ?? 5}
              onChange={(event) =>
                onChange({ ...value, max_results: Number(event.target.value) })
              }
            />
          </FormField>
          <FormField
            readOnly={readOnly}
            label={t("Include domains")}
            description={t(
              "Optional comma-separated DNS names. Each name includes its subdomains; no URLs or wildcards.",
            )}
          >
            <Input
              value={domains}
              onChange={(event) => {
                setDomains(event.target.value);
                onChange({
                  ...value,
                  allow_domains: event.target.value
                    .split(",")
                    .map((domain) => domain.trim())
                    .filter(Boolean),
                });
              }}
            />
          </FormField>
        </>
      )}
    </DisclosureSection>
  );
}
