import { FileMemoryBrowser } from "../memory/documents";
import type { BotAccount } from "./account";
import {
  ArrowLeftIcon,
  DotsThreeIcon,
  FileTextIcon,
  ListBulletsIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "a13n-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty, Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { MarkdownContent } from "../../shared/markdown";
import { Confirm } from "../../shared/dialogs";
import { Section } from "../../shared/page";
import styles from "./bots.module.css";
import { GroupMemorySettings, MemorySettings } from "./memory-settings";
import { GroupMemoryActions } from "./memory-toolbar";
import { refreshMemory } from "./memory-actions";
import { MemorySearchField, MemorySearchResults } from "./memory-search";
import { MemoryProvenance } from "./memory-provenance";

export function BotMemory({ account }: { account: BotAccount }) {
  const { can } = useWorkspace(),
    { t } = useTranslation();
  // Do not mount queries at all for a non-administrator, including direct route navigation.
  if (!can("bot_memory.read"))
    return (
      <Empty
        title={t("Administrator access required")}
        description={t(
          "Only workspace administrators can view and manage conversation memory.",
        )}
      />
    );
  return (
    <>
      {account.memory ? (
        <MemoryBrowser
          account={account}
          providerId={account.memory.provider_id}
        />
      ) : (
        <Empty
          title={t("Memory is not configured")}
          description={t(
            "Choose memory storage, then enable memory for the groups you select.",
          )}
          action={<MemorySettings account={account} setup />}
        />
      )}
    </>
  );
}

export function BotGroupMemory({
  account,
  target,
}: {
  account: BotAccount;
  target: Schema["AccountTarget"];
}) {
  const { can } = useWorkspace(),
    { t } = useTranslation();
  if (!can("bot_memory.read"))
    return (
      <Empty
        title={t("Administrator access required")}
        description={t(
          "Only workspace administrators can view and manage conversation memory.",
        )}
      />
    );
  if (!account.memory)
    return (
      <Empty
        title={t("Memory is not configured")}
        description={t(
          "Select a Memory Provider for this bot before enabling group memory.",
        )}
        action={<MemorySettings account={account} setup />}
      />
    );
  return (
    <GroupMemoryScope
      key={`${target.id}:${account.memory.provider_id}`}
      account={account}
      target={target}
      providerId={account.memory.provider_id}
    />
  );
}

function GroupMemoryScope({
  account,
  target,
  providerId,
}: {
  account: BotAccount;
  target: Schema["AccountTarget"];
  providerId: string;
}) {
  const client = useClient(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: [
      "bot-memory-scopes",
      account.id,
      providerId,
      "target",
      target.id,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/memory-scopes", {
          params: {
            path: { account_id: account.id },
            query: { provider_id: providerId, target_id: target.id, limit: 1 },
          },
          signal,
        })
        .then(data),
  });
  const scope = query.data?.items[0];
  return (
    <>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="detail" />
      ) : query.error ? null : scope ? (
        <MemoryBrowser
          account={account}
          providerId={providerId}
          fixedScope={scope}
          target={target}
        />
      ) : (
        <Empty
          title={t("Group memory is not configured")}
          description={t(
            "Configure this conversation to give it its own memory index.",
          )}
          action={<GroupMemorySettings account={account} target={target} />}
        />
      )}
    </>
  );
}

function MemoryBrowser({
  account,
  providerId,
  fixedScope,
  target,
}: {
  account: BotAccount;
  providerId: string;
  fixedScope?: Schema["Scope"];
  target?: Schema["AccountTarget"];
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [search, setSearch] = useSearchParams(),
    page = useCursor();
  const scopeId = fixedScope?.id ?? search.get("memory_scope") ?? "";
  const scopes = useQuery({
    enabled: !fixedScope,
    queryKey: ["bot-memory-scopes", account.id, providerId, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/memory-scopes", {
          params: {
            path: { account_id: account.id },
            query: { provider_id: providerId, cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  const items = fixedScope ? [fixedScope] : scopes.data?.items;
  const selected = items?.find((scope) => scope.id === scopeId);
  if (!fixedScope && scopes.isSuccess && !items?.length)
    return (
      <Empty
        title={t("Choose groups to enable memory")}
        description={t(
          "Memory storage is selected. Configure memory for each group; groups stay isolated unless you explicitly share.",
        )}
        action={<GroupMemorySettings account={account} />}
      />
    );
  return (
    <Section
      title={t("Conversation memory")}
      description={t(
        "Browse the index, then open only the documents you need.",
      )}
      actions={
        scopeId && (
          <GroupMemoryActions
            account={account}
            scopeId={scopeId}
            scope={selected}
            target={target}
          />
        )
      }
    >
      <ErrorNotice error={scopes.error} retry={() => void scopes.refetch()} />
      <div className={styles.memoryToolbar}>
        {!fixedScope && (
          <ChoiceField
            label={t("Conversation")}
            variant="filter"
            placeholder={t("Choose a conversation")}
            value={scopeId}
            options={
              items?.map((scope) => ({ value: scope.id, label: scope.name })) ??
              []
            }
            onValueChange={(value) => {
              const next = new URLSearchParams(search);
              next.set("memory_scope", value);
              next.delete("memory_doc");
              setSearch(next);
            }}
          />
        )}
        {scopeId && <MemorySearchField />}
      </div>
      {!fixedScope && !scopes.isPending && (
        <Pagination page={page} next={scopes.data?.next_cursor} />
      )}
      {scopeId ? (
        <ScopeDocuments
          key={`${scopeId}:${providerId}`}
          account={account}
          scopeId={scopeId}
          scopeName={selected?.name ?? t("Selected conversation")}
          scope={selected}
          target={target}
        />
      ) : (
        <Empty
          title={t("Choose a conversation")}
          description={t("Each channel or group has its own memory index.")}
        />
      )}
    </Section>
  );
}

function ScopeDocuments({
  target,
  ...props
}: Parameters<typeof NativeScopeDocuments>[0] & {
  target?: Schema["AccountTarget"];
}) {
  return props.scope?.backend_type === "a13n.filesystem" ? (
    <div className={styles.fileMemory}>
      <div className={styles.fileMemoryActions}>
        <GroupMemorySettings
          account={props.account}
          initialScope={props.scope}
          target={target}
        />
      </div>
      <FileMemoryBrowser conversationScopeId={props.scopeId} />
    </div>
  ) : (
    <NativeScopeDocuments {...props} />
  );
}

function NativeScopeDocuments({
  account,
  scopeId,
  scopeName,
  scope,
}: {
  account: BotAccount;
  scopeId: string;
  scopeName: string;
  scope?: Schema["Scope"];
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation(),
    [search, setSearch] = useSearchParams();
  const page = useCursor(),
    documentId = search.get("memory_doc") ?? "",
    date = search.get("memory_date") ?? "";
  const kind =
    search.get("memory_kind") === "daily"
      ? "daily"
      : search.get("memory_kind") === "long_term"
        ? "long_term"
        : undefined;
  const searching = !!search.get("memory_query");
  const path = { account_id: account.id, scope_id: scopeId };
  const listing = useQuery({
    enabled: !searching,
    queryKey: [
      "bot-memory-documents",
      account.id,
      scopeId,
      date,
      kind,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents",
          {
            params: {
              path,
              query: {
                activity_date: date || undefined,
                kind,
                cursor: page.cursor,
              },
            },
            signal,
          },
        )
        .then(data),
  });
  const index = useQuery({
    queryKey: ["bot-memory-index", account.id, scopeId],
    enabled: !documentId,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/index",
          { params: { path }, signal },
        )
        .then(data),
  });
  const document = useQuery({
    queryKey: ["bot-memory-document", account.id, scopeId, documentId],
    enabled: !!documentId,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/{document_id}",
          {
            params: { path: { ...path, document_id: documentId } },
            signal,
          },
        )
        .then(data),
  });
  function select(id: string) {
    const next = new URLSearchParams(search);
    if (id) next.set("memory_doc", id);
    else next.delete("memory_doc");
    setSearch(next);
  }
  function filter(key: string, value: string) {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    page.reset();
    setSearch(next);
  }
  const current =
    document.isFetching || document.error ? undefined : document.data;
  return (
    <div className={styles.memoryBrowser}>
      <aside className={styles.documentPane} aria-label={t("Memory documents")}>
        <button
          className={styles.documentItem}
          type="button"
          aria-pressed={!documentId}
          onClick={() => select("")}
        >
          <ListBulletsIcon aria-hidden="true" size={14} />
          <span>
            <strong>MEMORY.md</strong>
            <small>{t("Memory index")}</small>
          </span>
        </button>
        {searching ? (
          <MemorySearchResults
            accountId={account.id}
            scopeId={scopeId}
            onSelect={select}
          />
        ) : (
          <>
            <DisclosureSection
              title={<>{t("Filter documents")}</>}
              defaultOpen={!!(date || kind)}
            >
              <div className={styles.documentFilters}>
                <FormField label={t("Activity date")}>
                  <Input
                    type="date"
                    size="sm"
                    value={date}
                    onChange={(event) =>
                      filter("memory_date", event.target.value)
                    }
                  />
                </FormField>
                <ChoiceField
                  label={t("Kind")}
                  value={kind ?? "all"}
                  onValueChange={(value) =>
                    filter("memory_kind", value === "all" ? "" : value)
                  }
                  options={[
                    { value: "all", label: t("All kinds") },
                    { value: "daily", label: t("Daily") },
                    { value: "long_term", label: t("Long-term") },
                  ]}
                />
              </div>
            </DisclosureSection>
            <ErrorNotice
              error={listing.error}
              retry={() => void listing.refetch()}
            />
            {listing.isPending || listing.isFetching ? (
              <Loading variant="list" rows={4} />
            ) : (
              !listing.error &&
              listing.data?.items.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={styles.documentItem}
                  aria-pressed={documentId === item.id}
                  onClick={() => select(item.id)}
                >
                  <FileTextIcon aria-hidden="true" size={14} />
                  <span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.activity_date} ·{" "}
                      {t(
                        item.shared ? "Shared with this group" : "Local memory",
                      )}
                    </small>
                  </span>
                </button>
              ))
            )}
            {!listing.isPending &&
              !listing.error &&
              !listing.data?.items.length && (
                <p className={styles.listNote}>
                  {t("No documents match this view.")}
                </p>
              )}
            <Pagination page={page} next={listing.data?.next_cursor} />
          </>
        )}
      </aside>
      <section className={styles.contentPane} aria-label={t("Memory details")}>
        {!documentId ? (
          <>
            <header className={styles.contentHead}>
              <div>
                <h3>MEMORY.md</h3>
                <small>
                  {t(
                    scope?.visibility === "installation"
                      ? account.provider_key === "slack"
                        ? "Visible to all connected channels in this Slack workspace"
                        : "Visible to all connected groups in this Feishu enterprise"
                      : "Only this group",
                  )}
                </small>
              </div>
            </header>
            <ErrorNotice
              error={index.error}
              retry={() => void index.refetch()}
            />
            {index.isPending || index.isFetching ? (
              <Loading variant="list" rows={4} />
            ) : (
              !index.error &&
              index.data && (
                <div className={styles.index}>
                  <h2>{scopeName}</h2>
                  <p>{t("Read these documents for more detail.")}</p>
                  {index.data.entries.map((item) => (
                    <div key={item.id}>
                      <button type="button" onClick={() => select(item.id)}>
                        {item.title}
                      </button>
                      <p>{item.description}</p>
                    </div>
                  ))}
                  {index.data.next_cursor && (
                    <p>
                      {t(
                        "This index is partial. Browse document pages for more.",
                      )}
                    </p>
                  )}
                  {!index.data.entries.length && (
                    <p>{t("No memory documents are currently available.")}</p>
                  )}
                </div>
              )
            )}
          </>
        ) : (
          <>
            <header className={styles.contentHead}>
              <div>
                <Button variant="ghost" size="sm" onClick={() => select("")}>
                  <ArrowLeftIcon size={12} aria-hidden="true" />
                  {t("Back to index")}
                </Button>
                <h3>{current?.title ?? t("Memory document")}</h3>
              </div>
              {current && !current.shared && can("bot_memory.delete") && (
                <Menu>
                  <MenuTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        type="button"
                        aria-label={t("Document actions")}
                        title={t("Document actions")}
                      />
                    }
                  >
                    <DotsThreeIcon size={16} />
                  </MenuTrigger>
                  <MenuPopup align="end">
                    <Confirm
                      title={t("Delete memory")}
                      subject={current.title}
                      triggerElement={
                        <MenuItem closeOnClick={false} variant="destructive">
                          <TrashIcon size={14} />
                          {t("Delete")}
                        </MenuItem>
                      }
                      danger
                      description={t(
                        "Delete this memory for this group and every group that can read it. Previously delivered messages are not erased.",
                      )}
                      action={async () => {
                        await client.http.DELETE(
                          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/{document_id}",
                          {
                            params: {
                              path: { ...path, document_id: current.id },
                            },
                          },
                        );
                      }}
                      onSuccess={() => {
                        select("");
                        void refreshMemory(cache, account.id);
                      }}
                    />
                  </MenuPopup>
                </Menu>
              )}
            </header>
            <ErrorNotice
              error={document.error}
              retry={() => void document.refetch()}
            />
            {document.isPending || document.isFetching ? (
              <Loading variant="list" rows={4} />
            ) : (
              current && (
                <>
                  <div className={styles.metadata}>
                    <StatePill state={current.shared ? "shared" : "active"} />
                    <span>
                      {current.activity_date} · {current.timezone}
                    </span>
                    <span>
                      {t("Version")} {current.version}
                    </span>
                  </div>
                  <MemoryProvenance document={current} onOpen={select} />
                  <MarkdownContent text={current.text} />
                </>
              )
            )}
          </>
        )}
      </section>
    </div>
  );
}
