import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, Textarea } from "a13n-ui";
import { FileTextIcon, PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice, Loading } from "../../shared/feedback";
import { MarkdownContent } from "../../shared/markdown";
import { Confirm } from "../../shared/form";

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
  return (
    <section className="min-w-0 space-y-5" aria-label={t("File-based memory")}>
      <div className="space-y-2">
        <h2 className="text-base font-semibold">{t("File-based memory")}</h2>
        <p className="text-sm text-muted-foreground">
          {t(
            "Choose a saved memory store. Each environment keeps its own documents.",
          )}
        </p>
        <ErrorNotice error={scopes.error} retry={() => void scopes.refetch()} />
        {scopes.isPending ? (
          <Loading />
        ) : scopes.data?.items.length ? (
          <ChoiceField
            disabled={locked}
            label={t("Memory store")}
            value={scope}
            onValueChange={setScope}
            options={[
              { value: "", label: t("Choose a memory store"), disabled: true },
              ...scopes.data.items.map((item) => ({
                value: item.id,
                label: `${item.scope} · ${item.subject_id} · ${item.environment_id}`,
              })),
            ]}
          />
        ) : (
          !scopes.error && (
            <p className="text-sm text-muted-foreground">
              {t(
                "No saved file memory stores yet. Run an agent with file memory enabled first.",
              )}
            </p>
          )
        )}
        <div className="flex gap-2">
          {cursor && (
            <Button variant="outline" onClick={() => setCursor(undefined)}>
              {t("First page")}
            </Button>
          )}
          {scopes.data?.next_cursor && (
            <Button
              variant="outline"
              onClick={() => setCursor(scopes.data!.next_cursor!)}
            >
              {t("Next")}
            </Button>
          )}
        </div>
      </div>
      {scope && (
        <>
          <DocumentBrowser
            key={scope}
            scopeId={scope}
            locked={locked}
            setLocked={setLocked}
          />
          <OrganizationActivity key={`activity:${scope}`} scopeId={scope} />
        </>
      )}
    </section>
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
  return (
    <div className="grid min-h-96 min-w-0 overflow-hidden rounded-lg border lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside
        className="space-y-2 border-b bg-muted/30 p-3 lg:border-r lg:border-b-0"
        aria-label={t("Documents")}
      >
        <Button
          variant="outline"
          className="w-full"
          disabled={locked}
          onClick={() => {
            setCreating(true);
            setSelected("");
          }}
        >
          <PlusIcon aria-hidden="true" />
          {t("New document")}
        </Button>
        <ErrorNotice
          error={listing.error}
          retry={() => void listing.refetch()}
        />
        {listing.isPending ? (
          <Loading />
        ) : (
          listing.data?.items.map((item) => (
            <button
              key={item.id}
              type="button"
              disabled={locked}
              aria-pressed={selected === item.id}
              className={`flex w-full items-start gap-2 rounded-md p-2 text-left text-sm hover:bg-muted ${selected === item.id ? "bg-muted" : ""}`}
              onClick={() => {
                setSelected(item.id);
                setCreating(false);
              }}
            >
              <FileTextIcon className="mt-0.5 shrink-0" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block truncate font-medium">{item.title}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {item.path} · v{item.version}
                </span>
              </span>
            </button>
          ))
        )}
        {!listing.isPending &&
          !listing.error &&
          !listing.data?.items.length && (
            <p className="p-2 text-sm text-muted-foreground">
              {t("No documents yet.")}
            </p>
          )}
        <div className="flex gap-2">
          {cursor && (
            <Button variant="ghost" onClick={() => setCursor(undefined)}>
              {t("First page")}
            </Button>
          )}
          {listing.data?.next_cursor && (
            <Button
              variant="ghost"
              onClick={() => setCursor(listing.data!.next_cursor!)}
            >
              {t("Next")}
            </Button>
          )}
        </div>
      </aside>
      <div className="min-w-0 p-5 lg:p-7">
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
        ) : (
          <Empty
            title={t("Choose a document")}
            description={t(
              "Read documents, inspect revisions, or create a new memory.",
            )}
          />
        )}
      </div>
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
  return (
    <div className="space-y-5">
      <ErrorNotice
        error={document.error}
        retry={() => void document.refetch()}
      />
      {document.isPending ? (
        <Loading />
      ) : (
        value && (
          <>
            <header className="space-y-3">
              <div>
                <p className="text-xs text-muted-foreground">
                  {value.path} · v{value.version}
                </p>
                <h3 className="mt-1 text-xl font-semibold">{value.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {value.description}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={value.kind === "episodic" || version !== undefined}
                  onClick={() => setEditing(true)}
                >
                  {t("Edit")}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setShowHistory(!showHistory)}
                >
                  {t("Version history")}
                </Button>
                {version !== undefined && (
                  <Button
                    variant="outline"
                    onClick={() => setVersion(undefined)}
                  >
                    {t("Current version")}
                  </Button>
                )}
                <Confirm
                  title={t("Delete document")}
                  description={t(
                    "This deletes the document and all its revisions.",
                  )}
                  subject={value.title}
                  danger
                  trigger={t("Delete")}
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
              </div>
              {value.kind === "episodic" && (
                <p className="text-xs text-muted-foreground">
                  {t(
                    "Events are immutable. Create a new document to record a correction.",
                  )}
                </p>
              )}
            </header>
            {showHistory && (
              <div className="rounded-md border p-3">
                <ErrorNotice
                  error={history.error}
                  retry={() => void history.refetch()}
                />
                {history.isPending ? (
                  <Loading />
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {history.data?.map((item) => (
                      <Button
                        key={item.version}
                        variant="ghost"
                        onClick={() => setVersion(item.version)}
                      >
                        v{item.version} ·{" "}
                        {new Date(item.saved_at).toLocaleString()}
                      </Button>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">
                  {t("Showing up to 100 recent revisions.")}
                </p>
              </div>
            )}
            <MarkdownContent text={value.text} />
          </>
        )
      )}
    </div>
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
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="text-lg font-semibold">
        {t(initial ? "Edit document" : "New document")}
      </h3>
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
          className="font-mono text-sm"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </FormField>
      <ErrorNotice error={save.error} />
      {save.error && (
        <p className="text-sm text-muted-foreground">
          {t(
            "Your draft is preserved. If the document changed, cancel and reload before applying your edits again.",
          )}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending || !text.trim()}>
          {t(save.isPending ? "Saving…" : "Save")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={save.isPending}
          onClick={onCancel}
        >
          {t("Cancel")}
        </Button>
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
    <div className="space-y-3">
      <Button variant="outline" onClick={() => setOpen(!open)}>
        {t("Organization activity")}
      </Button>
      {open && (
        <>
          <ErrorNotice
            error={activity.error}
            retry={() => void activity.refetch()}
          />
          {activity.isPending ? (
            <Loading />
          ) : activity.data?.length ? (
            <div className="divide-y rounded-md border px-4">
              {activity.data.map((item) => (
                <div
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
                >
                  <span className="text-muted-foreground">{item.run_id}</span>
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
              <p className="text-sm text-muted-foreground">
                {t("No automatic organization work yet.")}
              </p>
            )
          )}
          <p className="text-xs text-muted-foreground">
            {t(
              "Showing up to 50 recent tasks. Deferred candidates are kept outside normal recall.",
            )}
          </p>
          <Button variant="ghost" onClick={() => void activity.refetch()}>
            {t("Refresh")}
          </Button>
        </>
      )}
    </div>
  );
}
