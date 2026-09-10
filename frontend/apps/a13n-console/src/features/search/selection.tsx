import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField, DisclosureSection, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { searchApi } from "./api";
import { providersPath } from "../providers/navigation";

export function AgentSearchSelection({
  value,
  onChange,
}: {
  value: Schema["SearchSelection"] | null;
  onChange: (value: Schema["SearchSelection"] | null) => void;
}) {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    client = useClient();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const [domains, setDomains] = useState(
    value?.include_domains?.join(", ") ?? "",
  );
  const query = useQuery({
    queryKey: ["search-providers", scope.kind, scope.id, "choices"],
    refetchOnWindowFocus: "always",
    queryFn: ({ signal }) =>
      allPages((cursor) => searchApi(client, scope).providers(signal, cursor)),
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
            include_domains: value?.include_domains ?? [],
          },
    );
  }
  return (
    <DisclosureSection
      title={t("Configure web search")}
      summary={
        selected?.name ?? t(value ? "Selected provider unavailable" : "Off")
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {t(
          "Choose one saved search provider. Web tools require an Environment and include search, fetch, and download. Search can consume provider quota.",
        )}
      </p>
      <ChoiceField
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
      {can("search_provider.manage") && (
        <a
          className="mt-2 mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          href={providersPath("search", "workspace", workspace.key)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("Manage search providers")}
          <ArrowSquareOutIcon size={13} aria-hidden="true" />
        </a>
      )}
      {value && (
        <>
          <FormField label={t("Maximum results")}>
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
            label={t("Include domains")}
            description={t(
              "Optional comma-separated DNS names, up to 20. No URLs or wildcards.",
            )}
          >
            <Input
              value={domains}
              onChange={(event) => {
                setDomains(event.target.value);
                onChange({
                  ...value,
                  include_domains: event.target.value
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
