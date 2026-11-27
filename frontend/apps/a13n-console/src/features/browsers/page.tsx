import { GlobeIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  data,
  ifMatch,
  matchesSearch,
  matchingPage,
  rowTag,
  type Schema,
} from "../../shared/api";
import {
  AppFilter,
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useAppFilterParam,
  useCursor,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import {
  FormActions,
  SchemaFields,
  withSchemaValues,
} from "../../shared/forms";
import { AppBadge, CopyableId } from "../../shared/identity";
import { Page, PageActions } from "../../shared/page";
import { ManageProvidersLink } from "../providers/manage-link";
import { useProviderTypes } from "../providers/kind-providers";
import { useBrowser } from "./api";

export function BrowsersPage() {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const [appId, setAppId] = useAppFilterParam();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string>();
  const hasSearch = Boolean(search.trim());
  const filtered = hasSearch || !!appId;
  const page = useCursor({ search, appId });
  const query = useQuery({
    queryKey: ["browsers", workspace.id, "list", page.cursor, search, appId],
    queryFn: ({ signal }) =>
      matchingPage(
        (cursor, limit) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/browsers", {
              params: { query: { cursor, limit, app_id: appId } },
              signal,
            })
            .then(data),
        page.cursor,
        hasSearch
          ? (item) =>
              matchesSearch(search.trim().toLowerCase(), item.name, item.id)
          : undefined,
      ),
    refetchInterval: 5000,
  });
  return (
    <Page
      title={t("Browsers")}
      description={t("Managed and external browsers for your agents.")}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search browsers")}
          filters={<AppFilter value={appId} onChange={setAppId} />}
        />
      }
    >
      <PageActions secondary>
        <ManageProvidersLink category="browsers" />
      </PageActions>
      <PageActions>
        {can("write") && (
          <CreateBrowser onCreated={(item) => setSelected(item.id)} />
        )}
      </PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Browsers")}
            items={query.data.items}
            onRowActivate={(item) => setSelected(item.id)}
            columns={[
              {
                label: t("Browser"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    badge={item.app_id && <AppBadge appId={item.app_id} />}
                    description={t(
                      item.source === "managed"
                        ? "Managed browser"
                        : "External CDP",
                    )}
                  />
                ),
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Control"),
                render: (item) => (
                  <span>
                    {t(`browser.control.${item.control}`, {
                      defaultValue: item.control,
                    })}
                  </span>
                ),
              },
              {
                label: t("Created"),
                render: (item) => <Timestamp value={item.created_at} />,
              },
            ]}
          />
          <CollectionFooter count={query.data.items.length}>
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<GlobeIcon />}
            title={t(filtered ? "No matching browsers" : "No browsers yet")}
            description={t(
              appId
                ? "Change or clear the search and filters."
                : hasSearch
                  ? "Try a different search."
                  : "Create a browser, then select it from Browser in your session.",
            )}
            action={
              !filtered && can("write") ? (
                <CreateBrowser onCreated={(item) => setSelected(item.id)} />
              ) : undefined
            }
          />
        )
      )}
      {selected && (
        <BrowserDetails id={selected} onClose={() => setSelected(undefined)} />
      )}
    </Page>
  );
}

export function CreateBrowser({
  onCreated,
}: {
  onCreated: (browser: Schema["Browser"]) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Create browser")}
      closeLabel={t("Close")}
      trigger={
        <Button>
          <PlusIcon />
          {t("Create browser")}
        </Button>
      }
    >
      {open && (
        <CreateBrowserForm close={() => setOpen(false)} onCreated={onCreated} />
      )}
    </ModalFrame>
  );
}

function CreateBrowserForm({
  close,
  onCreated,
}: {
  close: () => void;
  onCreated: (browser: Schema["Browser"]) => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace } = useWorkspace();
  const [name, setName] = useState("");
  const [providerId, setProviderId] = useState("");
  const [recipe, setRecipe] = useState<Record<string, unknown>>({});
  const [stop, setStop] = useState("1800");
  const [remove, setRemove] = useState("");
  const providers = useQuery({
    queryKey: ["browser-providers", workspace.id, "options"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/browser-providers", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const enabledProviders = providers.data?.filter((item) => item.enabled) ?? [];
  const provider = enabledProviders.find((item) => item.id === providerId);
  const types = useProviderTypes("browser", enabledProviders.length > 0);
  const definition = types.data?.items.find(
    (item) => item.type === provider?.type,
  );
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/browsers", {
          body: {
            name,
            provider_id: providerId,
            ...(definition?.browser_recipe_schema
              ? {
                  recipe: withSchemaValues(
                    definition.browser_recipe_schema,
                    recipe,
                  ),
                }
              : {}),
            ...(definition?.supports_stop
              ? {
                  stop_after_seconds: stop === "" ? null : Number(stop),
                  delete_after_seconds: remove === "" ? null : Number(remove),
                }
              : remove === ""
                ? {}
                : { delete_after_seconds: Number(remove) }),
          },
        })
        .then(data),
    onSuccess: (item) => {
      void cache.invalidateQueries({ queryKey: ["browsers", workspace.id] });
      onCreated(item);
      close();
    },
  });
  if (providers.isPending) return <Loading variant="form" rows={2} />;
  if (!providers.data)
    return (
      <ErrorNotice
        error={providers.error}
        retry={() => void providers.refetch()}
      />
    );
  if (!enabledProviders.length)
    return (
      <div className="flex flex-col items-start gap-4">
        <p>
          {t("Add or enable a browser provider before creating a browser.")}
        </p>
        <ManageProvidersLink category="browsers" />
      </div>
    );
  const canCreate = Boolean(name.trim() && definition && !save.isPending);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (canCreate) save.mutate();
      }}
    >
      <FormField label={t("Name")}>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={200}
          autoFocus
        />
      </FormField>
      <ChoiceField
        label={t("Provider")}
        placeholder={t("Select a provider")}
        value={provider?.id}
        onValueChange={(value) => {
          setProviderId(String(value));
          setRecipe({});
        }}
        options={enabledProviders.map((item) => ({
          value: item.id,
          label: item.name,
        }))}
      />
      {definition?.browser_recipe_schema && (
        <SchemaFields
          key={providerId}
          schema={definition.browser_recipe_schema}
          value={recipe}
          onChange={setRecipe}
        />
      )}
      {definition?.supports_stop && (
        <FormField
          label={t("Stop after idle (seconds)")}
          description={t(
            "Stopping keeps cookies and storage. Blank keeps it running.",
          )}
        >
          <Input
            type="number"
            min={60}
            max={604800}
            value={stop}
            onChange={(event) => setStop(event.target.value)}
          />
        </FormField>
      )}
      {definition && (
        <FormField
          label={t("Delete after idle (seconds)")}
          description={t(
            definition.supports_stop
              ? "Deleting removes the profile. Blank keeps it until you delete it."
              : "Deletes the browser. Default: 30 minutes.",
          )}
        >
          <Input
            type="number"
            min={60}
            max={604800}
            value={remove}
            onChange={(event) => setRemove(event.target.value)}
          />
        </FormField>
      )}
      {provider && types.isPending && <Loading />}
      <ErrorNotice error={types.error} retry={() => void types.refetch()} />
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        disabled={!canCreate}
        onCancel={close}
        label={t("Create browser")}
      />
    </form>
  );
}

function BrowserDetails({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation();
  const query = useBrowser(id);
  const terminalIdle =
    query.data?.idle_seconds ?? query.data?.delete_after_seconds;
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={query.data?.name ?? t("Browser")}
      closeLabel={t("Close")}
    >
      <ErrorNotice error={query.error} />
      {query.data ? (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <StatePill state={query.data.status} />
            {query.data.app_id && <AppBadge appId={query.data.app_id} />}
            <CopyableId value={id} />
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt className="text-muted-foreground">{t("Stop after idle")}</dt>
            <dd>
              {query.data.stop_after_seconds === null
                ? t("Never")
                : t("{{seconds}} seconds", {
                    seconds: query.data.stop_after_seconds,
                  })}
            </dd>
            <dt className="text-muted-foreground">{t("Delete after idle")}</dt>
            <dd>
              {terminalIdle == null
                ? t("Never")
                : t("{{seconds}} seconds", {
                    seconds: terminalIdle,
                  })}
            </dd>
          </dl>
          {query.data.failure && (
            <ErrorNotice error={new Error(query.data.failure)} />
          )}
          <BrowserLifecycle browser={query.data} />
        </div>
      ) : (
        <Loading variant="form" />
      )}
    </ModalFrame>
  );
}

export function BrowserLifecycle({
  browser,
  allowed = true,
  showDelete = true,
}: {
  browser: Schema["Browser"];
  allowed?: boolean;
  showDelete?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace, can } = useWorkspace();
  const providers = useQuery({
    queryKey: ["browser-providers", workspace.id, "options"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/browser-providers", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
    enabled: browser.source === "managed",
  });
  const types = useProviderTypes("browser", browser.source === "managed");
  const provider = providers.data?.find(
    (item) => item.id === browser.provider_id,
  );
  const supportsStop = types.data?.items.find(
    (item) => item.type === provider?.type,
  )?.supports_stop;
  const refresh = () => {
    void cache.invalidateQueries({ queryKey: ["browsers", workspace.id] });
  };
  const action = useMutation({
    mutationFn: (operation: "prepare" | "stop") =>
      client
        .workspace(workspace.id)
        .POST(`/api/v1/browsers/{browser_id}/${operation}`, {
          params: { path: { browser_id: browser.id } },
          headers: ifMatch(rowTag(browser)),
        })
        .then(data),
    onSuccess: refresh,
  });
  const terminal =
    browser.status === "deleted" || browser.status === "deleting";
  if (!allowed) return null;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {can("run") && ["reserved", "stopped"].includes(browser.status) && (
          <Button
            variant="outline"
            loading={action.isPending}
            onClick={() => action.mutate("prepare")}
          >
            {t("Start browser")}
          </Button>
        )}
        {can("write") &&
          browser.source === "managed" &&
          supportsStop === true &&
          browser.status === "ready" &&
          browser.control === "automation" && (
            <Button
              variant="outline"
              loading={action.isPending}
              onClick={() => action.mutate("stop")}
            >
              {t("Stop browser")}
            </Button>
          )}
        {showDelete && can("write") && !terminal && (
          <Confirm
            title={t("Delete browser")}
            subject={browser.name}
            description={t(
              "Permanently delete this browser, including its cookies and storage.",
            )}
            danger
            trigger={t("Delete browser")}
            action={() =>
              client
                .workspace(workspace.id)
                .POST("/api/v1/browsers/{browser_id}/release", {
                  params: { path: { browser_id: browser.id } },
                  headers: ifMatch(rowTag(browser)),
                })
                .then(data)
            }
            onSuccess={refresh}
          />
        )}
      </div>
      <ErrorNotice
        error={action.error}
        retry={() => {
          refresh();
          action.reset();
        }}
      />
    </div>
  );
}
