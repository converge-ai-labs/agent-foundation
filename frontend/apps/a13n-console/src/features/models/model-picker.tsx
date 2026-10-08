import { useQuery } from "@tanstack/react-query";
import { Button, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  DirectoryEmpty,
  DirectoryList,
  DirectoryRow,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { discoveryKey, modelApi } from "./api";
import { CatalogPicker } from "./catalog-picker";
import { CatalogNotice, type ModelDraft } from "./model-form";
import { ModelIcon } from "./model-icon";
import { ProviderAuthorization } from "./provider-authorization";

/** Account discovery and public catalog metadata are independent selection sources. */
export function ModelPicker({
  model,
  onSelected,
}: {
  model: ModelDraft;
  onSelected: () => void;
}) {
  if (model.definition?.supports_model_discovery && model.selectedProvider)
    return (
      <DiscoveredModels
        key={model.provider}
        model={model}
        onSelected={onSelected}
      />
    );
  return (
    <>
      <CatalogNotice model={model} />
      {model.catalog.isPending ? (
        <Loading variant="list" rows={5} />
      ) : (
        <CatalogPicker
          entries={model.catalog.data?.items ?? []}
          channels={model.channels}
          allowCompatible={model.selectedProvider?.type === "openai"}
          providerName={model.definition?.display_name}
          value={model.draft.catalog_ref}
          onSelect={(entry) => {
            model.chooseCatalog(entry);
            onSelected();
          }}
        />
      )}
    </>
  );
}

function DiscoveredModels({
  model,
  onSelected,
}: {
  model: ModelDraft;
  onSelected: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const api = modelApi(client, workspace.id);
  const [search, setSearch] = useState("");
  const oauth = !!model.definition?.oauth_scheme;
  const authorization = useQuery({
    queryKey: ["model-provider-authorization", workspace.id, model.provider],
    queryFn: ({ signal }) => api.authorization(model.provider, signal),
    enabled: oauth,
  });
  const ready =
    !oauth ||
    (!authorization.data?.pending &&
      (authorization.data?.state === "connected" ||
        authorization.data?.state === "refreshing"));
  const allowed = can("run");
  const models = useQuery({
    queryKey: discoveryKey(workspace.id, model.provider),
    queryFn: ({ signal }) => api.discoverModels(model.provider, signal),
    enabled: ready && allowed && !!model.selectedProvider?.enabled,
    retry: false,
  });
  const term = search.trim().toLocaleLowerCase();
  const entries =
    ready && allowed && !models.isError
      ? (models.data ?? []).filter((entry) =>
          `${entry.display_name} ${entry.slug}`
            .toLocaleLowerCase()
            .includes(term),
        )
      : [];
  return (
    <div className="grid gap-4">
      {oauth && model.selectedProvider && (
        <ProviderAuthorization provider={model.selectedProvider} />
      )}
      {model.definition?.oauth_scheme === "openai-chatgpt" && ready && (
        <p className="text-sm text-muted-foreground">
          {t("Using your ChatGPT plan")} ·{" "}
          <a
            href="https://chatgpt.com/settings/usage"
            className="font-medium text-foreground hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            {t("Manage usage")}
          </a>
        </p>
      )}
      {!allowed && (
        <p>
          {t(
            "Model discovery requires run permission. You can still enter a model ID manually.",
          )}
        </p>
      )}
      {allowed && ready && (
        <>
          <ErrorNotice error={models.error} />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="justify-self-end"
            disabled={models.isFetching}
            onClick={() => void models.refetch()}
          >
            {t(models.isError ? "Retry" : "Refresh models")}
          </Button>
          {models.isFetching && <Loading variant="list" rows={3} />}
        </>
      )}
      <DirectoryList
        search={
          <Input
            type="search"
            aria-label={t("Search models…")}
            placeholder={t("Search models…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        }
      >
        {entries.map((entry) => (
          <DirectoryRow
            key={entry.slug}
            icon={
              <ModelIcon
                upstream={entry.slug}
                provider={model.selectedProvider?.type}
                size={20}
              />
            }
            name={entry.display_name}
            detail={entry.slug}
            onClick={() => {
              model.chooseDiscovered(entry);
              onSelected();
            }}
          />
        ))}
        {ready && allowed && !models.isFetching && !entries.length && (
          <DirectoryEmpty>
            {t(
              models.isError
                ? "Model discovery unavailable. Enter a model ID to continue."
                : "No models found. You can still add a model manually.",
            )}
          </DirectoryEmpty>
        )}
        <DirectoryRow
          icon={<ModelIcon upstream="" size={20} />}
          name={t("Custom model")}
          detail={t("Enter an upstream model ID")}
          onClick={() => {
            model.chooseDiscovered(null);
            onSelected();
          }}
        />
      </DirectoryList>
    </div>
  );
}
