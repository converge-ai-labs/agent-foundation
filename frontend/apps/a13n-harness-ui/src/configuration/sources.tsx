import { createContext, useContext, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { Plus, ArrowLeft } from "@phosphor-icons/react";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import { SourceEditor } from "./editor";
import { ResourceFields } from "./fields";
import { resourceKinds, template, type ResourceKind } from "./documents";
import styles from "../shell/workbench.module.css";

export type SourceDraft = {
  content: string;
  base: string | null;
  digest: string | null;
  replacement: boolean;
};
// Deliberately memory-only. Kept above the auth gate so reauthentication does not discard edits.
export const DraftContext = createContext<Map<string, SourceDraft>>(new Map());

export function SourcesPage() {
  const sources = useSources();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [kind, setKind] = useState<ResourceKind>("model");
  const [id, setId] = useState("model-primary");
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  return (
    <>
      <PageHeader
        title="Resources"
        description="Manage the configuration used by new runs. Active runs keep their captured configuration."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus />
            New resource
          </Button>
        }
      />
      <ErrorNotice error={sources.error} retry={() => void sources.refetch()} />
      <TextField label="Find resources" value={search} onChange={setSearch} />
      {sources.isPending && <p role="status">Loading resources…</p>}
      <div className={styles.resourceList}>
        {sources.data?.sources
          .filter((source) =>
            `${source.relative_path} ${source.resource_ids.join(" ")}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((source) => (
            <Link
              key={source.relative_path}
              className={styles.resourceRow}
              to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
            >
              <div>
                <strong>
                  {source.resource_ids.join(", ") || source.relative_path}
                </strong>
                <small>{source.relative_path}</small>
              </div>
              <span>{source.resource_kind.replaceAll("_", " ")}</span>
              <small>
                {!source.writable
                  ? "Read only"
                  : !source.content_available
                    ? "Replacement only"
                    : "Editable"}
              </small>
            </Link>
          ))}
      </div>
      {sources.data && !sources.data.sources.length && (
        <Panel title="No accepted resources">
          <p>
            Complete guided setup to create the root configuration before adding
            resources.
          </p>
          <Link to="/setup">Open setup</Link>
        </Panel>
      )}
      <ModalFrame
        open={creating}
        onOpenChange={setCreating}
        title="New resource"
        description="Create a source document, then validate and publish it."
        closeLabel="Close"
        footer={
          <Button
            disabled={!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/.test(id)}
            onClick={() => {
              const choice = resourceKinds.find((item) => item.value === kind)!;
              const path = `${choice.directory}/${id}.${kind === "subagent" ? "md" : "yaml"}`;
              if (
                sources.data?.sources.some(
                  (source) => source.relative_path === path,
                ) ||
                drafts.has(path)
              ) {
                const unpublished =
                  drafts.get(path)?.digest === null &&
                  !sources.data?.sources.some(
                    (source) => source.relative_path === path,
                  );
                navigate(
                  `/settings/source?path=${encodeURIComponent(path)}${unpublished ? "&new=1" : ""}`,
                );
              } else {
                drafts.set(path, {
                  content: template(kind, id),
                  base: null,
                  digest: null,
                  replacement: true,
                });
                navigate(
                  `/settings/source?path=${encodeURIComponent(path)}&new=1`,
                );
              }
              setCreating(false);
            }}
          >
            Create draft
          </Button>
        }
      >
        <div className={styles.stack}>
          <ChoiceField
            label="Resource type"
            value={kind}
            options={resourceKinds}
            onValueChange={(value) => {
              const choice = resourceKinds.find(
                (item) => item.value === value,
              )!;
              setKind(choice.value);
              setId(`${choice.prefix}-new`);
            }}
          />
          <TextField
            label="Resource ID"
            value={id}
            onChange={setId}
            description="Use the resource prefix and a lowercase hyphenated name."
          />
        </div>
      </ModalFrame>
    </>
  );
}

export function SourcePage() {
  const [params] = useSearchParams();
  const path = params.get("path") ?? "";
  return (
    <SourceDocument key={path} path={path} isNew={params.get("new") === "1"} />
  );
}
function SourceDocument({ path, isNew }: { path: string; isNew: boolean }) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  const [draft, setDraft] = useState<SourceDraft | null>(
    () => drafts.get(path) ?? null,
  );
  const [notice, setNotice] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const source = useQuery({
    queryKey: ["source", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          signal,
        }),
      ),
    enabled: !!path && !isNew,
  });
  const dirty = !!draft && draft.content !== draft.base;
  const external =
    !!source.data &&
    !!draft?.digest &&
    source.data.source_digest !== draft.digest;
  const writable = isNew || source.data?.writable;
  const update = (next: SourceDraft) => {
    drafts.set(path, next);
    setDraft(next);
    setNotice("");
  };
  useEffect(() => {
    if (source.data && !draft) {
      const next = {
        content: source.data.content ?? "",
        base: source.data.content,
        digest: source.data.source_digest,
        replacement: false,
      };
      drafts.set(path, next);
      setDraft(next);
    }
  }, [source.data, draft, drafts, path]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const validate = useMutation({
    mutationFn: (content: string) =>
      result(
        client.POST("/api/configuration/validate", {
          params: { query: { path } },
          body: { content },
        }),
      ),
    onSuccess: () =>
      setNotice("Candidate validated. Nothing has been published yet."),
  });
  const save = useMutation({
    mutationFn: (content: string) =>
      result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          body: { content },
        }),
      ),
    onSuccess: (publication, submitted) => {
      const current = drafts.get(path)!;
      update({
        ...current,
        base: submitted,
        digest: publication.source_digest,
        replacement: false,
      });
      setNotice(
        `Source ${publication.action}. Accepted generation ${publication.generation_digest.slice(0, 12)}. Active runs are unchanged.`,
      );
      void queries.invalidateQueries();
      if (isNew)
        navigate(`/settings/source?path=${encodeURIComponent(path)}`, {
          replace: true,
        });
    },
  });
  const remove = useMutation({
    mutationFn: () =>
      result(
        client.DELETE("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      ),
    onSuccess: () => {
      drafts.delete(path);
      void queries.invalidateQueries();
      navigate("/settings/resources");
    },
  });
  const canEdit =
    !!draft &&
    !!writable &&
    (source.data?.content_available || isNew || draft.replacement);
  return (
    <>
      <Link className={styles.back} to="/settings/resources">
        <ArrowLeft />
        All resources
      </Link>
      <PageHeader
        title={path || "Resource"}
        description={
          dirty
            ? "Unsaved draft · retained in this tab while you navigate, not after a reload."
            : "Accepted source · validation and publication operate on the complete configuration."
        }
        actions={
          <>
            {source.data?.writable && source.data.resource_kind !== "root" && (
              <Button
                variant="destructive-outline"
                onClick={() => setDeleting(true)}
              >
                Delete source
              </Button>
            )}
            {canEdit && (
              <>
                <Button
                  variant="outline"
                  loading={validate.isPending}
                  onClick={() => validate.mutate(draft!.content)}
                >
                  Validate
                </Button>
                <Button
                  loading={save.isPending}
                  disabled={!dirty}
                  onClick={() => save.mutate(draft!.content)}
                >
                  Publish
                </Button>
              </>
            )}
          </>
        }
      />
      <ErrorNotice
        error={source.error || validate.error || save.error || remove.error}
      />
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {external && (
        <div className={styles.notice}>
          <p>
            The accepted source changed elsewhere. Your draft is retained.
            Publishing is last-write-wins, not an atomic conflict check.
          </p>
          <Button variant="outline" onClick={() => setReplacing(true)}>
            Load accepted version
          </Button>
        </div>
      )}
      {source.data && !source.data.content_available && !draft?.replacement && (
        <Panel title="Source content is not available">
          <p>
            This API intentionally does not return MCP source content. A
            replacement affects the entire file, including{" "}
            {source.data.resource_ids.join(", ")}. Existing secrets and unknown
            fields cannot be recovered here.
          </p>
          {writable && (
            <Button
              variant="outline"
              onClick={() =>
                update({
                  content: "",
                  base: null,
                  digest: source.data!.source_digest,
                  replacement: true,
                })
              }
            >
              Start explicit complete replacement
            </Button>
          )}
        </Panel>
      )}
      {canEdit && (
        <Panel>
          <ResourceFields
            source={draft!.content}
            onChange={(content) => update({ ...draft!, content })}
          />
          <details
            className={styles.details}
            open={draft!.replacement || path.endsWith(".md")}
          >
            <summary>Advanced source</summary>
            <p>
              Complete file replacement. Preserve unknown fields and use
              credential references, not literal keys.
            </p>
            <SourceEditor
              value={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          </details>
        </Panel>
      )}
      {source.data?.content_available && !writable && (
        <Panel>
          <SourceEditor value={source.data.content ?? ""} readOnly />
        </Panel>
      )}
      <ModalFrame
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this source?"
        description={`This deletes ${path} and every resource defined in it. References must be removed first. Unsaved edits will be discarded.`}
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Delete source
          </Button>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
      <ModalFrame
        open={replacing}
        onOpenChange={setReplacing}
        title="Discard this local draft?"
        description="Load the accepted source. Your unsaved edits will be lost."
        closeLabel="Cancel"
        footer={
          <Button
            onClick={() => {
              const accepted = source.data!;
              update({
                content: accepted.content ?? "",
                base: accepted.content,
                digest: accepted.source_digest,
                replacement: false,
              });
              setReplacing(false);
            }}
          >
            Load accepted version
          </Button>
        }
      >
        <p>{path}</p>
      </ModalFrame>
    </>
  );
}
