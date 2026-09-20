import { createContext, useContext, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  Button,
  ChoiceField,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  ModalFrame,
} from "a13n-ui";
import { Plus, ArrowLeft, DotsThree, Trash } from "@phosphor-icons/react";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, Panel, TextField } from "../shell/ui";
import { ConfirmAction } from "../shell/confirm-action";
import { SourceEditor } from "./editor";
import { ResourceFields } from "./fields";
import {
  readDocument,
  resourceKinds,
  template,
  updateDocument,
  type ResourceKind,
} from "./documents";
import { MediaUnderstandingFields } from "./media-understanding";
import styles from "../shell/workbench.module.css";

export type SourceDraft = {
  content: string;
  base: string | null;
  digest: string | null;
  replacement: boolean;
};
// Deliberately memory-only. Kept above the auth gate so reauthentication does not discard edits.
export const DraftContext = createContext<Map<string, SourceDraft>>(new Map());

export function NewResourceButton({
  kind,
  label,
  initial = {},
  variant = "default",
}: {
  kind: ResourceKind;
  label?: string;
  variant?: "default" | "outline" | "ghost";
  initial?: Record<string, unknown>;
}) {
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  return (
    <Button
      variant={variant}
      onClick={() => {
        const choice = resourceKinds.find((item) => item.value === kind)!;
        const id = `${choice.prefix}-${crypto.randomUUID().slice(0, 8)}`;
        const path = `${choice.directory}/${id}.${kind === "subagent" ? "md" : "yaml"}`;
        let content = template(kind, id);
        for (const [key, value] of Object.entries(initial))
          content = updateDocument(content, [key], value);
        drafts.set(path, {
          content,
          base: null,
          digest: null,
          replacement: true,
        });
        navigate(`/settings/source?path=${encodeURIComponent(path)}&new=1`);
      }}
    >
      <Plus />
      {label ??
        `Add ${resourceKinds.find((item) => item.value === kind)!.label.toLowerCase()}`}
    </Button>
  );
}

export function DraftLinks({
  kinds,
  search = "",
}: {
  kinds?: ResourceKind[];
  search?: string;
}) {
  const drafts = useContext(DraftContext);
  const sources = useSources();
  return (
    <div className={styles.resourceList}>
      {[...drafts.entries()]
        .filter(
          ([path, draft]) =>
            draft.digest === null &&
            !sources.data?.sources.some(
              (source) => source.relative_path === path,
            ) &&
            (!kinds ||
              kinds.includes(
                (readDocument(draft.content)?.get("kind") ??
                  (path.endsWith(".md") ? "subagent" : "")) as ResourceKind,
              )) &&
            `${path} ${draft.content}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .map(([path, draft]) => (
          <Link
            key={path}
            className={styles.resourceRow}
            to={`/settings/source?path=${encodeURIComponent(path)}&new=1`}
          >
            <div>
              <strong>
                {String(readDocument(draft.content)?.get("name") ?? path)}
              </strong>
              <small>{path}</small>
            </div>
            <span>Unsaved draft</span>
          </Link>
        ))}
    </div>
  );
}

export function SourcesPage({
  kinds,
  title = "Advanced configuration",
  description,
  headingLevel = 1,
}: {
  kinds?: ResourceKind[];
  title?: string;
  description?: string;
  headingLevel?: 1 | 2;
}) {
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
        title={title}
        level={headingLevel}
        description={description}
        actions={
          kinds ? (
            kinds.map((kind) => <NewResourceButton key={kind} kind={kind} />)
          ) : (
            <Button onClick={() => setCreating(true)}>
              <Plus />
              Add configuration
            </Button>
          )
        }
      />
      <ErrorNotice error={sources.error} retry={() => void sources.refetch()} />
      <div className={styles.search}>
        <TextField
          type="search"
          label={`Search ${title.toLowerCase()}`}
          value={search}
          onChange={setSearch}
        />
      </div>
      {sources.isPending && <p role="status">Loading resources…</p>}
      <DraftLinks kinds={kinds} search={search} />
      <div className={styles.resourceList}>
        {sources.data?.sources
          .filter(
            (source) =>
              !kinds || kinds.includes(source.resource_kind as ResourceKind),
          )
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
        <Panel title="No saved configuration">
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
        title="Add configuration"
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
            Continue
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
export function SourceDocument({
  path,
  isNew = false,
  title,
  mediaOnly = false,
  embedded = false,
}: {
  path: string;
  isNew?: boolean;
  title?: string;
  mediaOnly?: boolean;
  embedded?: boolean;
}) {
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
  const sharedDraft = drafts.get(path);
  useEffect(() => {
    // Quick actions can publish this source while its clean editor stays mounted.
    if (!dirty && sharedDraft && sharedDraft !== draft) setDraft(sharedDraft);
  }, [dirty, sharedDraft, draft]);
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
    onSuccess: () => setNotice("Configuration valid · not saved"),
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
      setNotice("Saved");
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
  const resourceLabel =
    resourceKinds
      .find(
        (item) =>
          item.value === readDocument(draft?.content ?? "")?.get("kind"),
      )
      ?.label.toLowerCase() ??
    (path.endsWith(".md") ? "subagent" : "configuration");
  return (
    <div className={styles.sourceDocument}>
      {!embedded && (
        <Link className={styles.back} to="/settings">
          <ArrowLeft />
          Settings
        </Link>
      )}
      <PageHeader
        level={mediaOnly ? 2 : 1}
        description={
          mediaOnly ? "Used when native input is unavailable." : undefined
        }
        title={
          title ||
          (isNew
            ? `Add ${resourceLabel}`
            : String(
                readDocument(draft?.content ?? "")?.get("name") ||
                  path ||
                  "Configuration",
              ))
        }
        actions={
          source.data?.writable &&
          source.data.resource_kind !== "root" && (
            <Menu>
              <MenuTrigger
                aria-label="More configuration actions"
                render={<Button variant="ghost" size="icon" />}
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="end">
                <MenuItem
                  variant="destructive"
                  onClick={() => setDeleting(true)}
                >
                  <Trash />
                  Delete configuration
                </MenuItem>
              </MenuPopup>
            </Menu>
          )
        }
      />
      <ErrorNotice error={source.error || remove.error} />
      {external && (
        <div className={styles.notice}>
          <p>
            This configuration changed elsewhere. Your edits are preserved.
            Saving will replace the current file.
          </p>
          <Button variant="outline" onClick={() => setReplacing(true)}>
            Reload saved version
          </Button>
        </div>
      )}
      {source.data && !source.data.content_available && !draft?.replacement && (
        <Panel title="Saved configuration is hidden">
          <p>
            Saved MCP configuration may contain secrets. Replacing it overwrites
            the whole file, including {source.data.resource_ids.join(", ")}.
            Existing secrets and unknown fields cannot be recovered here.
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
              Replace configuration file
            </Button>
          )}
        </Panel>
      )}
      {canEdit && (
        <div className={styles.configurationForm}>
          {mediaOnly ? (
            <MediaUnderstandingFields
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          ) : (
            <ResourceFields
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          )}
          {!mediaOnly && (
            <details
              className={styles.details}
              open={
                (!readDocument(draft!.content) && draft!.replacement) ||
                path.endsWith(".md")
              }
            >
              <summary>Advanced configuration</summary>
              <div className={styles.stack}>
                {readDocument(draft!.content)?.has("kind") && (
                  <TextField
                    label="Resource ID"
                    value={String(
                      readDocument(draft!.content)?.get("id") ?? "",
                    )}
                    description="Changing this ID can break references."
                    onChange={(id) =>
                      update({
                        ...draft!,
                        content: updateDocument(draft!.content, ["id"], id),
                      })
                    }
                  />
                )}
                <h3>Configuration file</h3>
                <p>
                  Complete file replacement. Preserve unknown fields and use
                  credential references, not literal keys.
                </p>
                <SourceEditor
                  value={draft!.content}
                  onChange={(content) => update({ ...draft!, content })}
                />
                <Button
                  variant="outline"
                  loading={validate.isPending}
                  disabled={save.isPending}
                  onClick={() => validate.mutate(draft!.content)}
                >
                  Check configuration
                </Button>
              </div>
            </details>
          )}
        </div>
      )}
      {canEdit && (
        <footer
          className={styles.editorActions}
          aria-label="Configuration actions"
        >
          {(validate.error || save.error) && (
            <div className={styles.editorError}>
              <ErrorNotice error={validate.error || save.error} />
            </div>
          )}
          <div className={styles.editorState}>
            <span>
              {dirty
                ? "Unsaved changes · lost on reload"
                : "No unsaved changes"}
            </span>
            {notice ? (
              <small role="status">{notice}</small>
            ) : (
              <small>Saving validates changes for future runs.</small>
            )}
          </div>
          <div className={styles.actions}>
            {isNew ? (
              <ConfirmAction
                key={path}
                trigger={
                  <Button variant="ghost" disabled={save.isPending}>
                    Cancel
                  </Button>
                }
                title="Discard this unsaved configuration?"
                description={`Your unsaved configuration for ${path} will be lost.`}
                confirmLabel="Discard draft"
                destructive
                onConfirm={() => {
                  drafts.delete(path);
                  navigate("/settings/resources");
                }}
              />
            ) : (
              <Button
                variant="ghost"
                disabled={!dirty || save.isPending}
                onClick={() => setReplacing(true)}
              >
                {mediaOnly ? "Discard" : "Cancel"}
              </Button>
            )}
            <Button
              loading={save.isPending}
              disabled={!dirty}
              onClick={() => save.mutate(draft!.content)}
            >
              {isNew
                ? `Create ${resourceLabel}`
                : mediaOnly
                  ? "Save"
                  : "Save changes"}
            </Button>
          </div>
        </footer>
      )}
      {source.data?.content_available && !writable && (
        <Panel>
          <SourceEditor value={source.data.content ?? ""} readOnly />
        </Panel>
      )}
      <ModalFrame
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this configuration?"
        description={`This deletes ${path} and every resource defined in it. References must be removed first. Unsaved edits will be discarded.`}
        closeLabel="Cancel"
        footer={
          <Button
            variant="destructive"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Delete configuration
          </Button>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
      <ModalFrame
        open={replacing}
        onOpenChange={setReplacing}
        title="Discard this local draft?"
        description="Reload the saved configuration. Your unsaved edits will be lost."
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
            Reload saved version
          </Button>
        }
      >
        <p>{path}</p>
      </ModalFrame>
    </div>
  );
}
