import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  Textarea,
} from "a13n-ui";
import {
  DotsThreeIcon,
  FileTextIcon,
  PlusIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { MarkdownContent } from "../../shared/markdown";
import styles from "./memory.module.css";

/** A saved file memory store: its documents at the left, the open one at the right. */
export function FileMemoryBrowser({
  conversationScopeId,
}: {
  conversationScopeId?: string;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [scope, setScope] = useState("");
  const [locked, setLocked] = useState(false);
  const [cursor, setCursor] = useState<string>();
  const scopes = useQuery({
    queryKey: ["file-memory-scopes", workspace.id, conversationScopeId, cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/memory-scopes", {
          params: {
            path: { workspace: workspace.id },
            query: { conversation_scope_id: conversationScopeId, cursor },
          },
          signal,
        })
        .then(data),
  });
  const items = scopes.data?.items ?? [];
  return (
    <div className={styles.files} aria-label={t("File-based memory")}>
      <div className={styles.filesToolbar}>
        {scopes.isPending ? (
          <Loading variant="status" />
        ) : items.length ? (
          <ChoiceField
            variant="filter"
            disabled={locked}
            label={t("Memory store")}
            placeholder={t("Choose a memory store")}
            value={scope}
            onValueChange={setScope}
            options={items.map((item) => ({
              value: item.id,
              label: `${item.scope} · ${item.subject_id} · ${item.environment_id}`,
            }))}
          />
        ) : null}
        {cursor && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCursor(undefined)}
          >
            {t("First page")}
          </Button>
        )}
        {scopes.data?.next_cursor && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCursor(scopes.data!.next_cursor!)}
          >
            {t("Next")}
          </Button>
        )}
      </div>
      <ErrorNotice error={scopes.error} retry={() => void scopes.refetch()} />
      {scope ? (
        <>
          <DocumentBrowser
            key={scope}
            scopeId={scope}
            locked={locked}
            setLocked={setLocked}
          />
          <OrganizationActivity key={`activity:${scope}`} scopeId={scope} />
        </>
      ) : (
        !scopes.isPending &&
        !scopes.error && (
          <Empty
            icon={<FileTextIcon aria-hidden="true" />}
            title={t(
              items.length ? "Choose a memory store" : "No memory stores yet",
            )}
            description={t(
              items.length
                ? "Each environment keeps its own documents. Nothing is combined across stores."
                : "No saved file memory stores yet. Run an agent with file memory enabled first.",
            )}
          />
        )
      )}
    </div>
  );
}

function DocumentBrowser({
  scopeId,
  locked,
  setLocked,
}: {
  scopeId: string;
  locked: boolean;
  setLocked: (value: boolean) => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [selected, setSelected] = useState(""),
    [creating, setCreating] = useState(false);
  const [cursor, setCursor] = useState<string>();
  const path = { workspace: workspace.id, scope_id: scopeId };
  const listing = useQuery({
    queryKey: ["file-documents", scopeId, cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents",
          { params: { path, query: { cursor } }, signal },
        )
        .then(data),
  });
  const refresh = async () => {
    await cache.invalidateQueries({ queryKey: ["file-documents", scopeId] });
  };
  const documents = listing.data?.items ?? [];
  // One offer of the primary action: the empty store offers it in the pane.
  const emptyStore = listing.isSuccess && !documents.length;
  function create() {
    setCreating(true);
    setSelected("");
  }
  const newDocument = (
    <Button variant="ghost" size="sm" disabled={locked} onClick={create}>
      <PlusIcon aria-hidden="true" />
      {t("New document")}
    </Button>
  );
  return (
    <div className={styles.browser}>
      <nav
        className={`${styles.tree} a13n-scrollbar`}
        aria-label={t("Documents")}
      >
        <p className={styles.treeHeading}>
          {t("Documents")}
          {!emptyStore && <span>{documents.length}</span>}
        </p>
        {listing.isPending ? (
          <Loading variant="list" rows={3} />
        ) : (
          documents.map((item) => (
            <button
              key={item.id}
              type="button"
              className={styles.fileButton}
              disabled={locked}
              aria-pressed={selected === item.id}
              aria-current={selected === item.id ? "true" : undefined}
              title={item.path}
              onClick={() => {
                setSelected(item.id);
                setCreating(false);
              }}
            >
              <FileTextIcon size={14} aria-hidden="true" />
              <span>
                <strong>{item.title}</strong>
                <small>
                  {item.path} · v{item.version}
                </small>
              </span>
            </button>
          ))
        )}
        <ErrorNotice
          error={listing.error}
          retry={() => void listing.refetch()}
        />
        <div className={styles.treeActions}>
          {newDocument}
          {cursor && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCursor(undefined)}
            >
              {t("First page")}
            </Button>
          )}
          {listing.data?.next_cursor && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCursor(listing.data!.next_cursor!)}
            >
              {t("Next")}
            </Button>
          )}
        </div>
      </nav>
      <section className={styles.document} aria-label={t("Memory document")}>
        {creating ? (
          <DocumentEditor
            setLocked={setLocked}
            key="new"
            scopeId={scopeId}
            onSaved={async (id) => {
              await refresh();
              setCreating(false);
              setSelected(id);
            }}
            onCancel={() => setCreating(false)}
          />
        ) : selected ? (
          <DocumentDetail
            setLocked={setLocked}
            key={selected}
            scopeId={scopeId}
            documentId={selected}
            onDeleted={async () => {
              setSelected("");
              await refresh();
            }}
          />
        ) : emptyStore ? (
          <Empty
            icon={<FileTextIcon aria-hidden="true" />}
            title={t("No documents yet")}
            description={t(
              "Write the first memory yourself, or let the agent save one.",
            )}
          />
        ) : (
          <p className={styles.notice}>
            {t("Choose a document to read, edit, or inspect its revisions.")}
          </p>
        )}
      </section>
    </div>
  );
}

function DocumentDetail({
  scopeId,
  documentId,
  onDeleted,
  setLocked,
}: {
  setLocked: (value: boolean) => void;
  scopeId: string;
  documentId: string;
  onDeleted: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [editing, setEditing] = useState(false),
    [showHistory, setShowHistory] = useState(false),
    [version, setVersion] = useState<number>();
  const path = {
    workspace: workspace.id,
    scope_id: scopeId,
    document_id: documentId,
  };
  const document = useQuery({
    queryKey: ["file-document", scopeId, documentId, version],
    queryFn: async ({ signal }) => {
      const response = await client.http.GET(
        "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents/{document_id}",
        { params: { path, query: { version } }, signal },
      );
      return {
        document: data(response),
        etag: response.response.headers.get("ETag") ?? "",
      };
    },
  });
  const history = useQuery({
    queryKey: ["file-history", scopeId, documentId],
    enabled: showHistory,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents/{document_id}/revisions",
          { params: { path, query: { limit: 100 } }, signal },
        )
        .then(data),
  });
  const value = document.data?.document;
  if (editing && document.data)
    return (
      <DocumentEditor
        setLocked={setLocked}
        scopeId={scopeId}
        initial={document.data}
        onCancel={() => setEditing(false)}
        onSaved={async () => {
          setEditing(false);
          await cache.invalidateQueries({
            queryKey: ["file-document", scopeId, documentId],
          });
          await cache.invalidateQueries({
            queryKey: ["file-documents", scopeId],
          });
          await cache.invalidateQueries({
            queryKey: ["file-history", scopeId, documentId],
          });
        }}
      />
    );
  if (document.isPending)
    return (
      <div className={styles.documentBody}>
        <Loading variant="detail" />
      </div>
    );
  return (
    <>
      {value && (
        <header className={styles.documentHeader}>
          <span className={styles.documentPath}>
            <h3>{value.title}</h3>
            <small title={value.path}>
              {value.path} · v{value.version}
            </small>
          </span>
          <div className={styles.documentActions}>
            <Button
              variant="ghost"
              size="sm"
              disabled={value.kind === "episodic" || version !== undefined}
              onClick={() => setEditing(true)}
            >
              {t("Edit")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowHistory(!showHistory)}
            >
              {t("Version history")}
            </Button>
            {version !== undefined && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setVersion(undefined)}
              >
                {t("Current version")}
              </Button>
            )}
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
                  title={t("Delete document")}
                  description={t(
                    "This deletes the document and all its revisions.",
                  )}
                  subject={value.title}
                  danger
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <TrashIcon size={14} />
                      {t("Delete")}
                    </MenuItem>
                  }
                  action={() =>
                    client.http
                      .DELETE(
                        "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents/{document_id}",
                        { params: { path } },
                      )
                      .then(data)
                  }
                  onSuccess={() => void onDeleted()}
                />
              </MenuPopup>
            </Menu>
          </div>
        </header>
      )}
      <div className={styles.documentBody}>
        <ErrorNotice
          error={document.error}
          retry={() => void document.refetch()}
        />
        {value && (
          <>
            {value.description && (
              <p className={styles.notice}>{value.description}</p>
            )}
            {value.kind === "episodic" && (
              <p className={styles.notice}>
                {t(
                  "Events are immutable. Create a new document to record a correction.",
                )}
              </p>
            )}
            {showHistory && (
              <div className={styles.revisions}>
                <ErrorNotice
                  error={history.error}
                  retry={() => void history.refetch()}
                />
                {history.isPending ? (
                  <Loading variant="list" rows={2} />
                ) : (
                  <>
                    {history.data?.map((item) => (
                      <Button
                        key={item.version}
                        variant="ghost"
                        size="sm"
                        onClick={() => setVersion(item.version)}
                      >
                        v{item.version} · <Timestamp value={item.saved_at} />
                      </Button>
                    ))}
                    {!!history.data?.length && (
                      <span className={styles.notice}>
                        {t("Showing up to 100 recent revisions.")}
                      </span>
                    )}
                  </>
                )}
              </div>
            )}
            <MarkdownContent text={value.text} />
          </>
        )}
      </div>
    </>
  );
}

function DocumentEditor({
  scopeId,
  initial,
  onSaved,
  onCancel,
  setLocked,
}: {
  setLocked: (value: boolean) => void;
  scopeId: string;
  initial?: { document: Schema["ManagedMemoryDocument"]; etag: string };
  onSaved: (id: string) => Promise<void>;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [title, setTitle] = useState(initial?.document.title ?? ""),
    [description, setDescription] = useState(
      initial?.document.description ?? "",
    ),
    [text, setText] = useState(initial?.document.text ?? "");
  const [kind, setKind] = useState<"semantic" | "procedural" | "episodic">(
      initial?.document.kind ?? "semantic",
    ),
    [filename, setFilename] = useState("");
  const key = useMemo(
    () => crypto.randomUUID(),
    [title, description, text, kind, filename],
  );
  useEffect(() => {
    setLocked(true);
    return () => setLocked(false);
  }, [setLocked]);
  const save = useMutation({
    mutationFn: async () => {
      const path = { workspace: workspace.id, scope_id: scopeId };
      const result = initial
        ? await client.http
            .PUT(
              "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents/{document_id}",
              {
                params: {
                  path: { ...path, document_id: initial.document.id },
                  header: { "Idempotency-Key": key, "If-Match": initial.etag },
                },
                body: {
                  expected_version: initial.document.version,
                  change: { type: "replace", text },
                },
              },
            )
            .then(data)
        : await client.http
            .POST(
              "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/documents",
              {
                params: { path, header: { "Idempotency-Key": key } },
                body: {
                  title,
                  description,
                  text,
                  kind,
                  path: `${kind}/${filename}.md`,
                },
              },
            )
            .then(data);
      await onSaved(result.document.id);
    },
  });
  return (
    <form
      className={styles.documentForm}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <header className={styles.documentHeader}>
        <span className={styles.documentPath}>
          <h3>{t(initial ? "Edit document" : "New document")}</h3>
          {initial && (
            <small title={initial.document.path}>
              {initial.document.path} · v{initial.document.version}
            </small>
          )}
        </span>
      </header>
      <div className={styles.documentFields}>
        {!initial && (
          <>
            <ChoiceField
              label={t("Memory kind")}
              value={kind}
              onValueChange={(value) => setKind(value as typeof kind)}
              options={[
                { value: "semantic", label: t("Knowledge") },
                { value: "procedural", label: t("Procedure") },
                { value: "episodic", label: t("Event") },
              ]}
            />
            <FormField label={t("Title")}>
              <Input
                required
                maxLength={160}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </FormField>
            <FormField label={t("Description")}>
              <Input
                maxLength={320}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </FormField>
            <FormField
              label={t("File name")}
              description={`${kind}/${filename || "project-decisions"}.md`}
            >
              <Input
                required
                pattern="[a-zA-Z0-9_-]+"
                value={filename}
                onChange={(e) => setFilename(e.target.value)}
                placeholder="project-decisions"
              />
            </FormField>
          </>
        )}
        <FormField label={t("Markdown content")}>
          <Textarea
            required
            rows={16}
            className={styles.documentText}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </FormField>
        <ErrorNotice error={save.error} />
        {save.error && (
          <p className={styles.notice}>
            {t(
              "Your draft is preserved. If the document changed, cancel and reload before applying your edits again.",
            )}
          </p>
        )}
        <FormActions
          variant="outline"
          pending={save.isPending}
          disabled={!text.trim()}
          label={t("Save")}
          onCancel={onCancel}
        />
      </div>
    </form>
  );
}

function OrganizationActivity({ scopeId }: { scopeId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const activity = useQuery({
    queryKey: ["memory-organization", scopeId],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/memory-scopes/{scope_id}/organization",
          {
            params: { path: { workspace: workspace.id, scope_id: scopeId } },
            signal,
          },
        )
        .then(data),
  });
  return (
    <div className={styles.activity}>
      <div className={styles.activityActions}>
        <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
          {t("Organization activity")}
        </Button>
        {open && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void activity.refetch()}
          >
            {t("Refresh")}
          </Button>
        )}
      </div>
      {open && (
        <>
          <ErrorNotice
            error={activity.error}
            retry={() => void activity.refetch()}
          />
          {activity.isPending ? (
            <Loading variant="list" rows={2} />
          ) : activity.data?.length ? (
            <div className={styles.activityRows}>
              {activity.data.map((item) => (
                <div key={item.id} className={styles.activityRow}>
                  <span>{item.run_id}</span>
                  <span>
                    {t(item.status)} ·{" "}
                    {t("{{count}} saved", { count: item.committed })}
                    {(item.deferred ?? 0) > 0
                      ? ` · ${t("{{count}} deferred", { count: item.deferred })}`
                      : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            !activity.error && (
              <p className={styles.notice}>
                {t("No automatic organization work yet.")}
              </p>
            )
          )}
          <p className={styles.notice}>
            {t(
              "Showing up to 50 recent tasks. Deferred candidates are kept outside normal recall.",
            )}
          </p>
        </>
      )}
    </div>
  );
}
