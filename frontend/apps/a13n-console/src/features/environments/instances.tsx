import { MonitorIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  data,
  matchesSearch,
  matchingPage,
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
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import { Page, PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { ManageProvidersLink } from "../providers/manage-link";
import {
  createManagedEnvironment,
  environmentApi,
  environmentTemplates,
} from "./api";
import instanceStyles from "./environments.module.css";
import { EnvironmentPanel } from "./instance-details";

const STATUSES = [
  "reserved",
  "creating",
  "starting",
  "ready",
  "stopping",
  "stopped",
  "deleting",
] as const;

/** The environments that exist right now, with their lifecycle state. */
export function EnvironmentInstances() {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [selected, setSelected] = useState<Schema["EnvironmentView"]>();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<(typeof STATUSES)[number]>();
  const term = search.trim().toLocaleLowerCase();
  const filtered = !!term || !!status;
  const page = useCursor({ term, status });
  const query = useQuery({
    queryKey: ["environments", workspace.id, page.cursor, term, status],
    queryFn: ({ signal }) =>
      matchingPage(
        (cursor, limit) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/environments", {
              params: { query: { cursor, limit, status } },
              signal,
            })
            .then(data),
        page.cursor,
        term
          ? (item) => matchesSearch(term, item.name, item.endpoint)
          : undefined,
      ),
    refetchInterval: 15_000,
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, workspace.id).providers(signal, cursor),
      ),
    enabled: can("read"),
  });
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
    enabled: can("read"),
  });
  const providerById = new Map(
    providers.data?.map((provider) => [provider.id, provider]),
  );
  const templateById = new Map(
    templates.data?.map((template) => [template.id, template]),
  );
  function templateName(item: Schema["EnvironmentView"]) {
    if (!item.template_id) return t("External target");
    const template = templateById.get(item.template_id);
    if (template) return template.name;
    return templates.isPending ? <InlineLoading width="6rem" /> : t("Managed");
  }
  return (
    <Page
      title={t("Environment instances")}
      description={t("Inspect the environments your agents are using.")}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search environments")}
          filters={
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={status ?? "all"}
              onValueChange={(value) =>
                setStatus(STATUSES.find((item) => item === value))
              }
              options={[
                { value: "all", label: t("All statuses") },
                ...STATUSES.map((value) => ({
                  value,
                  label: t(`state.${value}`),
                })),
              ]}
            />
          }
        />
      }
    >
      <PageActions secondary>
        <ManageProvidersLink category="environments" />
      </PageActions>
      <PageActions>{can("write") && <CreateEnvironment />}</PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Environment instances")}
            items={query.data.items}
            onRowActivate={(item) => setSelected(item)}
            columns={[
              {
                label: t("Environment"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    icon={
                      <MonitorIcon
                        aria-hidden="true"
                        className="size-4 text-muted-foreground"
                      />
                    }
                    description={templateName(item)}
                  />
                ),
              },
              {
                label: t("Provider"),
                render: (item) => {
                  // An external target is reached at its own endpoint.
                  if (item.endpoint) return <span>{item.endpoint}</span>;
                  const provider = item.provider_id
                    ? providerById.get(item.provider_id)
                    : undefined;
                  if (!provider)
                    return providers.isPending &&
                      providers.fetchStatus !== "idle" ? (
                      <InlineLoading width="5rem" />
                    ) : (
                      <span className={styles.muted}>{t("Not available")}</span>
                    );
                  return (
                    <span className={instanceStyles.providerCell}>
                      <ProviderIcon type={provider.type} />
                      <span>{provider.name}</span>
                    </span>
                  );
                },
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Activity"),
                render: (item) => <Timestamp value={item.last_used_at} />,
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) => <Timestamp value={item.updated_at} />,
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} environments on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<MonitorIcon aria-hidden="true" />}
            title={
              filtered
                ? t("No matching environments")
                : t("No environments yet")
            }
            description={
              filtered
                ? t("Change or clear the search and filters.")
                : t(
                    "Choose an environment template when starting a conversation, or register an external environment.",
                  )
            }
            action={!filtered && can("write") && <CreateEnvironment />}
          />
        )
      )}
      {selected && (
        <EnvironmentPanel
          key={selected.id}
          environment={selected}
          open
          onClose={() => setSelected(undefined)}
        />
      )}
    </Page>
  );
}

function CreateEnvironment() {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button type="button">
          <PlusIcon aria-hidden="true" />
          {t("Create environment")}
        </Button>
      }
      size={"md"}
      placement="top"
      title={t("Create environment")}
      description={t(
        "Allocate from a template or connect an externally managed target.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && <EnvironmentForm close={() => setOpen(false)} />}
    </ModalFrame>
  );
}

function EnvironmentForm({ close }: { close: () => void }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
  });
  const [kind, setKind] = useState("managed"),
    [name, setName] = useState(""),
    [templateId, setTemplateId] = useState(""),
    [endpoint, setEndpoint] = useState(""),
    [token, setToken] = useState("");
  const save = useMutation({
    mutationFn: () => {
      const named = name.trim() ? { name: name.trim() } : {};
      if (kind === "managed") {
        if (!templateId) throw new Error(t("Select an environment template."));
        return createManagedEnvironment(client, workspace.id, {
          template_id: templateId,
          ...named,
        });
      }
      const body: Schema["ExternalTargetCreate"] = {
        endpoint: endpoint.trim(),
        token,
        ...named,
      };
      return client
        .workspace(workspace.id)
        .POST("/api/v1/environments", {
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      close();
    },
  });
  const templateOptions =
    templates.data
      ?.filter((item) => item.enabled)
      .map((item) => ({
        value: item.id,
        label: item.name,
        description: item.description ?? undefined,
        badge: t("Version {{version}}", { version: item.version }),
      })) ?? [];
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField
        label={t("Name")}
        description={t("Leave empty to generate a name.")}
      >
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <FormField label={t("Ownership")}>
        <SearchPicker
          label={t("Ownership")}
          placeholder={t("Select ownership")}
          emptyMessage={t("No options")}
          value={kind}
          onValueChange={setKind}
          groups={[
            {
              label: t("Ownership"),
              options: [
                {
                  value: "managed",
                  label: t("Managed from template"),
                  description: t(
                    "Service allocates and retires the target for you.",
                  ),
                },
                {
                  value: "external",
                  label: t("External target"),
                  description: t(
                    "Register a target you run and keep control of.",
                  ),
                },
              ],
            },
          ]}
        />
      </FormField>
      <ErrorNotice error={templates.error} />
      {kind === "managed" ? (
        <FormField label={t("Template")}>
          <SearchPicker
            label={t("Template")}
            placeholder={t("Select template")}
            emptyMessage={t("No matching templates")}
            value={templateId}
            onValueChange={setTemplateId}
            groups={[{ label: t("Templates"), options: templateOptions }]}
          />
        </FormField>
      ) : (
        <>
          <FormField
            label={t("Endpoint URL")}
            description={t(
              "The origin the envd daemon serves, such as https://build-box.example.com:8443. Plain HTTP is accepted only for a loopback address.",
            )}
          >
            <Input
              required
              type="url"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
            />
          </FormField>
          <FormField
            label={t("Token")}
            description={t(
              "The token the daemon accepts. It is stored encrypted and never shown again.",
            )}
          >
            <Input
              required
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </FormField>
        </>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Create environment")}
      />
    </form>
  );
}
