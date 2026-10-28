import { ModelIdentity } from "../shell/model-identity";
import { createContext, useContext, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  ModalFrame,
} from "a13n-ui";
import { Plus, ArrowLeft, DotsThree, Trash } from "@phosphor-icons/react";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import {
  EmptyList,
  ErrorNotice,
  PageHeader,
  Panel,
  SearchField,
} from "../shell/ui";
import { inlineLabel } from "../shell/text";
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
import { useRootConfiguration } from "./root-configuration";
import { MediaUnderstandingFields } from "./media-understanding";
import {
  EnvdConfigurationFields,
  ExecutionDefaultsFields,
} from "./envd-fields";
import styles from "../shell/workbench.module.css";
import layout from "./settings.module.css";
import {
  SettingsChoice as ChoiceField,
  SettingsSection,
  SettingsTextField as TextField,
} from "../shell/settings";

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
        `Add ${inlineLabel(resourceKinds.find((item) => item.value === kind)!.label)}`}
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
  title = "Configuration files",
  description = kinds
    ? undefined
    : "The YAML and Markdown files behind every setting.",
  headingLevel = 1,
}: {
  kinds?: ResourceKind[];
  title?: string;
  description?: string;
  headingLevel?: 1 | 2;
}) {
  const sources = useSources();
  const selectors = useSelectors();
  const root = useRootConfiguration(kinds?.includes("agent"));
  const names = new Map([
    ...(selectors.data?.agents ?? []).map(
      (item) => [item.agent_id, item.name] as const,
    ),
    ...(selectors.data?.models ?? []).map(
      (item) => [item.model_id, item.name] as const,
    ),
    ...(selectors.data?.environments ?? []).map(
      (item) => [item.profile_id, item.name] as const,
    ),
    ...(selectors.data?.harness_plugins ?? []).map(
      (item) => [item.resource_id, item.name] as const,
    ),
    ...(selectors.data?.mcp_servers ?? []).map(
      (item) => [item.resource_id, item.name] as const,
    ),
    ...(selectors.data?.environment_run_extensions ?? []).map(
      (item) => [item.resource_id, item.name] as const,
    ),
  ]);
  const displayName = (source: {
    relative_path: string;
    resource_ids: string[];
  }) =>
    source.resource_ids.map((id) => names.get(id) ?? id).join(", ") ||
    source.relative_path;
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [kind, setKind] = useState<ResourceKind>("model");
  const listed = sources.data?.sources.filter(
    (source) => !kinds || kinds.includes(source.resource_kind as ResourceKind),
  );
  const [id, setId] = useState("model-primary");
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  const visible = listed?.filter((source) =>
    `${displayName(source)} ${source.relative_path} ${source.resource_ids.join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const addAction = kinds ? (
    kinds.map((kind) => (
      <NewResourceButton
        key={kind}
        kind={kind}
        variant={headingLevel === 2 ? "outline" : "default"}
      />
    ))
  ) : (
    <Button onClick={() => setCreating(true)}>
      <Plus />
      Add configuration
    </Button>
  );
  return (
    <section
      className={[
        headingLevel === 2 ? layout.collection : layout.collectionPage,
        !kinds && layout.configurationFiles,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <PageHeader
        title={title}
        level={headingLevel}
        description={description}
        actions={addAction}
      />
      <div className={layout.collection}>
        {!!listed?.length && (
          <div className={layout.toolbar}>
            {!!listed?.length && (
              <SearchField
                label={`Search ${inlineLabel(title)}`}
                value={search}
                onChange={setSearch}
              />
            )}
          </div>
        )}
        <ErrorNotice
          error={sources.error || selectors.error}
          retry={() => {
            void sources.refetch();
            void selectors.refetch();
          }}
        />
        {sources.isPending && <p role="status">Loading resources…</p>}
        <DraftLinks kinds={kinds} search={search} />
        <div className={styles.resourceList}>
          {visible?.map((source) => (
            <Link
              key={source.relative_path}
              className={styles.resourceRow}
              to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
            >
              <div>
                <strong>
                  {kinds ? displayName(source) : source.relative_path}
                </strong>
                <small>
                  {kinds?.length === 1 && kinds[0] === "agent" ? (
                    source.resource_ids.map((id) => {
                      const model = selectors.data?.agents.find(
                        (agent) => agent.agent_id === id,
                      )?.model_id;
                      return model ? (
                        <ModelIdentity
                          key={id}
                          model={selectors.data?.models?.find(
                            (item) => item.model_id === model,
                          )}
                          name={names.get(model) ?? model}
                        />
                      ) : (
                        "No model selected"
                      );
                    })
                  ) : kinds ? (
                    source.resource_ids.join(", ") === displayName(source) ? (
                      ""
                    ) : (
                      source.resource_ids.join(", ")
                    )
                  ) : displayName(source) === source.relative_path ? (
                    ""
                  ) : source.resource_kind === "model" ? (
                    <ModelIdentity
                      model={selectors.data?.models?.find((model) =>
                        source.resource_ids.includes(model.model_id),
                      )}
                      name={displayName(source)}
                    />
                  ) : (
                    displayName(source)
                  )}
                </small>
              </div>
              {source.resource_kind === "agent" &&
                source.resource_ids.includes(
                  root.configuration?.defaults?.agent,
                ) && <span className={styles.tag}>Default</span>}
              {!source.writable && (
                <span className={styles.tag}>Read only</span>
              )}
              {source.writable && !source.content_available && (
                <span className={styles.tag}>Replacement only</span>
              )}
              {!kinds && (
                <span className={styles.kind}>
                  {source.resource_kind.replaceAll("_", " ")}
                </span>
              )}
            </Link>
          ))}
        </div>
        {!!listed?.length && !visible?.length && (
          <EmptyList>No matching {inlineLabel(title)}.</EmptyList>
        )}
        {kinds && listed?.length === 0 && (
          <p
            className={
              headingLevel === 2 ? layout.emptySection : styles.emptyList
            }
          >
            No {inlineLabel(title)} yet. Add one to get started.
          </p>
        )}
        {sources.data && !kinds && !sources.data.sources.length && (
          <Panel title="No saved configuration">
            <p>Finish setup before adding configurations.</p>
            <Link to="/setup">Open setup</Link>
          </Panel>
        )}
      </div>
      <ModalFrame
        open={creating}
        onOpenChange={setCreating}
        title="Add configuration"
        closeLabel="Close"
        footer={
          <>
            <Button variant="outline" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button
              disabled={!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/.test(id)}
              onClick={() => {
                const choice = resourceKinds.find(
                  (item) => item.value === kind,
                )!;
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
          </>
        }
      >
        <SettingsSection>
          <ChoiceField
            label="Type"
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
            label="ID"
            value={id}
            onChange={setId}
            description="Use the resource prefix and a lowercase hyphenated name."
          />
        </SettingsSection>
      </ModalFrame>
    </section>
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
  section,
}: {
  path: string;
  isNew?: boolean;
  /** Null omits the heading when the containing section already supplies one. */
  title?: string | null;
  mediaOnly?: boolean;
  embedded?: boolean;
  section?: "envd" | "execution" | "connections";
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  const [draft, setDraft] = useState<SourceDraft | null>(
    () => drafts.get(path) ?? null,
  );
  const [notice, setNotice] = useState("");
  const [modelReady, setModelReady] = useState(false);
  const kind = readDocument(draft?.content ?? "")?.get("kind");
  const [back, backLabel] =
    kind === "agent"
      ? ["/settings/agents", "Agents"]
      : kind === "model"
        ? ["/settings/models", "Models"]
        : kind === "device"
          ? ["/settings/environments?tab=devices", "Devices"]
          : kind === "environment_profile" ||
              kind === "environment_run_extension"
            ? ["/settings/environments?tab=profiles", "Environments"]
            : kind === "mcp_server" || kind === "harness_plugin"
              ? ["/settings/connections", "Connections"]
              : ["/settings/resources", "Configuration files"];
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
      navigate(back);
    },
  });
  const canEdit =
    !!draft &&
    !!writable &&
    (source.data?.content_available || isNew || draft.replacement);
  const resourceLabel = inlineLabel(
    resourceKinds.find(
      (item) => item.value === readDocument(draft?.content ?? "")?.get("kind"),
    )?.label ?? (path.endsWith(".md") ? "subagent" : "configuration"),
  );
  return (
    <div
      className={`${styles.sourceDocument} ${section || mediaOnly ? styles.sourceSubsection : ""}`}
    >
      {title !== null && (
        <div className={styles.sourceHeader}>
          {!embedded && (
            <Link className={styles.back} to={back}>
              <ArrowLeft />
              {backLabel}
            </Link>
          )}
          <PageHeader
            level={mediaOnly || section ? 2 : 1}
            description={
              mediaOnly
                ? "Used when your main model can't read this media."
                : section === "envd"
                  ? "Server-wide settings. Save and restart Harness UI to apply."
                  : section === "execution"
                    ? "Used by new chats unless the project sets its own defaults."
                    : undefined
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
                      Delete
                    </MenuItem>
                  </MenuPopup>
                </Menu>
              )
            }
          />
        </div>
      )}
      <ErrorNotice error={source.error || remove.error} />
      {external && (
        <div className={styles.notice}>
          <p>
            This file changed elsewhere. Your edits are kept. Saving will
            replace the newer file.
          </p>
          <Button variant="outline" onClick={() => setReplacing(true)}>
            Reload saved
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
              Replace file
            </Button>
          )}
        </Panel>
      )}
      {canEdit && (
        <div className={styles.configurationForm}>
          {section === "envd" ? (
            <EnvdConfigurationFields
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          ) : section === "execution" ? (
            <ExecutionDefaultsFields
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          ) : mediaOnly ? (
            <MediaUnderstandingFields
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          ) : (
            <ResourceFields
              onReady={setModelReady}
              connectionsOnly={section === "connections"}
              source={draft!.content}
              onChange={(content) => update({ ...draft!, content })}
            />
          )}
          {!mediaOnly &&
            !(
              embedded &&
              !section &&
              source.data?.resource_kind === "root"
            ) && (
              <details
                className={styles.details}
                open={
                  (!readDocument(draft!.content) && draft!.replacement) ||
                  path.endsWith(".md")
                }
              >
                <summary>Advanced configuration</summary>
                <div className={styles.sourceAdvanced}>
                  {readDocument(draft!.content)?.has("kind") && (
                    <TextField
                      label="ID"
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
                  <section className={styles.sourceCode}>
                    <header>
                      <h3>Configuration file</h3>
                      <p>Use credential references for API keys.</p>
                    </header>
                    <SourceEditor
                      value={draft!.content}
                      onChange={(content) => update({ ...draft!, content })}
                    />
                  </section>
                  <Button
                    variant="outline"
                    loading={validate.isPending}
                    disabled={save.isPending}
                    onClick={() => validate.mutate(draft!.content)}
                  >
                    Check settings
                  </Button>
                </div>
              </details>
            )}
        </div>
      )}
      {!dirty && !isNew && !validate.error && !save.error && notice && (
        <p role="status">{notice}</p>
      )}
      {canEdit && (isNew || dirty || validate.error || save.error) && (
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
            {dirty && <span>Unsaved changes</span>}
            {notice && <small role="status">{notice}</small>}
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
                title="Discard draft?"
                description={`Changes to ${path} will be lost.`}
                confirmLabel="Discard draft"
                destructive
                onConfirm={() => {
                  drafts.delete(path);
                  navigate(back);
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
              disabled={!dirty || (kind === "model" && !modelReady)}
              onClick={() => save.mutate(draft!.content)}
            >
              {isNew ? "Create" : "Save"}
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
        closeLabel="Close confirmation"
        footer={
          <>
            <Button variant="outline" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              loading={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete
            </Button>
          </>
        }
      >
        <ErrorNotice error={remove.error} />
      </ModalFrame>
      <ModalFrame
        open={replacing}
        onOpenChange={setReplacing}
        title="Discard this local draft?"
        description="Reload the saved file? Your unsaved edits will be lost."
        closeLabel="Close confirmation"
        footer={
          <>
            <Button variant="outline" onClick={() => setReplacing(false)}>
              Keep editing
            </Button>
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
              Reload saved
            </Button>
          </>
        }
      >
        <p>{path}</p>
      </ModalFrame>
    </div>
  );
}
