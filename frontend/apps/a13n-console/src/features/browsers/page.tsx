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
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { CopyableId } from "../../shared/identity";
import { Page, PageActions } from "../../shared/page";
import { ManageProvidersLink } from "../providers/manage-link";
import { useBrowser } from "./api";

export function BrowsersPage() {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string>();
  const page = useCursor({ search });
  const query = useQuery({
    queryKey: ["browsers", workspace.id, "list", page.cursor, search],
    queryFn: ({ signal }) =>
      matchingPage(
        (cursor, limit) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/browsers", {
              params: { query: { cursor, limit } },
              signal,
            })
            .then(data),
        page.cursor,
        search.trim()
          ? (item) =>
              matchesSearch(search.trim().toLowerCase(), item.name, item.id)
          : undefined,
      ),
    refetchInterval: 5000,
  });
  return (
    <Page
      title={t("Browsers")}
      description={t(
        "Private browser profiles that stay with your agents across runs.",
      )}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search browsers")}
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
                    description={t(
                      item.source === "managed"
                        ? "Private profile"
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
            title={t(search ? "No matching browsers" : "No browsers yet")}
            description={t(
              "Create a browser, then select it in a debug session's Browser menu.",
            )}
            action={
              !search && can("write") ? (
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
      description={t(
        "A browser owns a private profile. Stopping keeps its cookies and storage; deleting removes them.",
      )}
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
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/browsers", {
          body: {
            name,
            provider_id: providerId,
            stop_after_seconds: stop === "" ? null : Number(stop),
            delete_after_seconds: remove === "" ? null : Number(remove),
          },
        })
        .then(data),
    onSuccess: (item) => {
      void cache.invalidateQueries({ queryKey: ["browsers", workspace.id] });
      onCreated(item);
      close();
    },
  });
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
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
        value={providerId}
        onValueChange={(value) => setProviderId(String(value))}
        options={(providers.data ?? [])
          .filter((item) => item.enabled)
          .map((item) => ({ value: item.id, label: item.name }))}
      />
      {!providers.isPending &&
        !providers.data?.some((item) => item.enabled) && (
          <ManageProvidersLink category="browsers" />
        )}
      <FormField
        label={t("Stop after idle (seconds)")}
        description={t(
          "Leave blank to keep running. The default is 30 minutes.",
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
      <FormField
        label={t("Delete after idle (seconds)")}
        description={t(
          "Leave blank to retain the profile until you delete it. This timer is independent of stopping.",
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
      <ErrorNotice error={save.error ?? providers.error} />
      <FormActions
        pending={save.isPending}
        disabled={!name.trim() || !providerId}
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
            <CopyableId value={id} />
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt className="text-muted-foreground">{t("Runtime generation")}</dt>
            <dd>{query.data.generation}</dd>
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
          <p className="text-sm text-muted-foreground">
            {t(
              "Select this browser in a debug session to take control. Starting a retained browser preserves its profile and begins a new runtime generation.",
            )}
          </p>
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
              "Permanently remove this browser and its private profile. Browsers in use cannot be deleted.",
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
