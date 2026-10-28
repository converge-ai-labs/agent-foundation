import { ModelIdentity } from "../shell/model-identity";
import { useContext, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import {
  Button,
  Skeleton,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { EmptyList, ErrorNotice, PageHeader, SearchField } from "../shell/ui";
import { SettingsTextField as TextField } from "../shell/settings";
import { DraftContext, DraftLinks, SourceDocument } from "./sources";
import { mediaKinds, mediaLabels } from "./media-understanding";
import { ModelEditor } from "./model-editor";
import { readDocument, updateDocument } from "./documents";
import { DotsThree, Plus, Copy, Image } from "@phosphor-icons/react";
import management from "./management.module.css";
import layout from "./settings.module.css";
import styles from "../shell/workbench.module.css";

export function ModelsPage() {
  const selectors = useSelectors();
  const sources = useSources();
  const { client } = useTransport();
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const queries = useQueryClient();
  const root = sources.data?.sources.find(
    (source) => source.resource_kind === "root",
  );
  const setDefault = useMutation({
    mutationFn: async ({
      kind,
      modelId,
    }: {
      kind: (typeof mediaKinds)[number];
      modelId: string;
    }) => {
      if (!root?.writable) throw new Error("Global settings are read-only.");
      const path = root.relative_path;
      const draft = drafts.get(path);
      if (draft && draft.content !== draft.base)
        throw new Error("Save or discard your global settings changes first.");
      const saved = await result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      );
      if (!saved.content) throw new Error("Couldn't load global settings.");
      const current = drafts.get(path);
      if (current && current.content !== current.base)
        throw new Error("Save or discard your global settings changes first.");
      const value =
        selectors.data?.media_understanding?.[kind] === modelId
          ? null
          : modelId;
      const content = updateDocument(
        saved.content,
        ["media_understanding", kind],
        value,
      );
      const publication = await result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          body: { content },
        }),
      );
      const latest = drafts.get(path);
      drafts.set(path, {
        content:
          latest && latest.content !== latest.base ? latest.content : content,
        base: content,
        digest: publication.source_digest,
        replacement: false,
      });
    },
    onSuccess: () => {
      void queries.invalidateQueries();
    },
  });
  const clone = useMutation({
    mutationFn: async ({ path, name }: { path: string; name: string }) => {
      const saved = await result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      );
      if (!saved.content)
        throw new Error("This model source is unavailable for cloning.");
      const id = `model-${crypto.randomUUID().slice(0, 8)}`;
      const content = updateDocument(
        updateDocument(saved.content, ["id"], id),
        ["name"],
        `${name} copy`,
      );
      const destination = `models/${id}.yaml`;
      drafts.set(destination, {
        content,
        base: null,
        digest: null,
        replacement: true,
      });
      navigate(
        `/settings/source?path=${encodeURIComponent(destination)}&new=1`,
      );
    },
  });
  const filteredModels = (selectors.data?.models ?? []).filter((model) =>
    `${model.name} ${model.route} ${model.model_id}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className={layout.collectionPage}>
      <PageHeader
        title="Models"
        description="Choose the models your agents can use."
        actions={
          <AddModelButton
            variant="default"
            onSaved={() => void queries.invalidateQueries()}
          />
        }
      />
      <div className={layout.collection}>
        <div className={layout.toolbar}>
          <SearchField
            label="Search models"
            value={search}
            onChange={setSearch}
          />
        </div>
        <ErrorNotice
          error={
            selectors.error || sources.error || clone.error || setDefault.error
          }
        />
        <DraftLinks kinds={["model"]} search={search} />
        {selectors.isPending && (
          <div role="status" aria-label="Loading models">
            <Skeleton className="h-20 w-full" />
          </div>
        )}
        <div className={management.modelList}>
          {filteredModels.map((model) => {
            const source = sources.data?.sources.find(
              (item) =>
                item.resource_kind === "model" &&
                item.resource_ids.includes(model.model_id),
            );
            return (
              <div
                key={model.model_id}
                className={`${styles.resourceRow} ${management.modelRow}`}
              >
                <div className={management.resourceIdentity}>
                  <div className={management.resourceInfo}>
                    {source ? (
                      <Link
                        className={management.modelLink}
                        to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                      >
                        <strong>
                          <ModelIdentity model={model} />
                        </strong>
                      </Link>
                    ) : (
                      <strong>
                        <ModelIdentity model={model} />
                      </strong>
                    )}
                    <small>{model.route}</small>
                    {mediaKinds.some(
                      (kind) =>
                        selectors.data?.media_understanding?.[kind] ===
                        model.model_id,
                    ) && (
                      <small>
                        Default:{" "}
                        {mediaKinds
                          .filter(
                            (kind) =>
                              selectors.data?.media_understanding?.[kind] ===
                              model.model_id,
                          )
                          .map((kind) => mediaLabels[kind])
                          .join(", ")}
                      </small>
                    )}
                  </div>
                </div>
                <div className={management.resourceMeta}>
                  {source?.content_available && (
                    <ModelCredentialSummary path={source.relative_path} />
                  )}
                  <small className={management.metadata}>
                    <span>
                      {selectors.data?.agents
                        .filter((agent) => agent.model_id === model.model_id)
                        .map((agent) => agent.name)
                        .join(", ") || "No agents using this model"}
                    </span>
                  </small>
                </div>
                {source && !source.writable && (
                  <span className={styles.tag}>Read only</span>
                )}
                <Menu>
                  <MenuTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Actions for ${model.name}`}
                      />
                    }
                  >
                    <DotsThree />
                  </MenuTrigger>
                  <MenuPopup align="end">
                    {source?.content_available &&
                      source.resource_ids.length === 1 && (
                        <MenuItem
                          disabled={clone.isPending}
                          onClick={() =>
                            clone.mutate({
                              path: source.relative_path,
                              name: model.name,
                            })
                          }
                        >
                          <Copy aria-hidden="true" />
                          Clone model
                        </MenuItem>
                      )}
                    {mediaKinds.map((kind) => (
                      <MenuItem
                        key={kind}
                        disabled={
                          !root?.writable ||
                          setDefault.isPending ||
                          !model.media_capabilities?.includes(kind)
                        }
                        onClick={() =>
                          setDefault.mutate({ kind, modelId: model.model_id })
                        }
                      >
                        <Image aria-hidden="true" />
                        {selectors.data?.media_understanding?.[kind] ===
                        model.model_id
                          ? "Clear"
                          : "Default"}{" "}
                        {mediaLabels[kind].toLowerCase()} understanding
                      </MenuItem>
                    ))}
                  </MenuPopup>
                </Menu>
              </div>
            );
          })}
        </div>
        {!!selectors.data?.models?.length && !filteredModels.length && (
          <EmptyList>No matching models.</EmptyList>
        )}
        {selectors.data && !selectors.data.models?.length && (
          <EmptyList>
            No models yet. Add a subscription or API connection to get started.
          </EmptyList>
        )}
      </div>
      {root && (
        <details className={layout.disclosure}>
          <summary>
            <span>Media models</span>
            <small>Used when your main model can't read media.</small>
          </summary>
          <SourceDocument
            key={root.relative_path}
            path={root.relative_path}
            title={null}
            mediaOnly
            embedded
          />
        </details>
      )}
    </div>
  );
}

function ModelCredentialSummary({ path }: { path: string }) {
  const { client } = useTransport();
  const source = useQuery({
    queryKey: ["source", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          signal,
        }),
      ),
  });
  const auth = readDocument(source.data?.content ?? "")?.toJS().authentication;
  const keys = useQuery({
    queryKey: ["keys"],
    enabled: auth?.kind === "api_key" && !!auth.credential_ref,
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
  });
  if (source.error || keys.error)
    return <small>Credential status unavailable</small>;
  if (auth?.kind !== "api_key") return null;
  return (
    <small className={management.metadata}>
      <span>
        {auth.env
          ? `Environment · ${auth.env}`
          : keys.data
            ? keys.data.some(
                (key) => key.credential_ref === auth.credential_ref,
              )
              ? "API key saved"
              : "API key missing"
            : "API key reference"}
      </span>
    </small>
  );
}

/** Add a Model without discarding the Agent draft or selecting an unsaved ID. */
export function AddModelButton({
  onSaved,
  variant = "outline",
  compact = false,
}: {
  onSaved: (id: string) => void;
  variant?: "default" | "outline";
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant={compact ? "ghost" : variant}
        size={compact ? "icon-sm" : "default"}
        aria-label="Add model"
        title={compact ? "Add model" : undefined}
        onClick={() => setOpen(true)}
      >
        <Plus aria-hidden="true" />
        {!compact && "Add model"}
      </Button>
      {open && (
        <AddModelDialog onClose={() => setOpen(false)} onSaved={onSaved} />
      )}
    </>
  );
}
function AddModelDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const title = useRef<HTMLSpanElement>(null);
  const { client } = useTransport();
  const queries = useQueryClient();
  const [id] = useState(() => `model-${crypto.randomUUID().slice(0, 8)}`);
  const [name, setName] = useState("");
  const [recipe, setRecipe] = useState<Schema<"ModelRecipe"> | null>(null);
  const [recipeReady, setRecipeReady] = useState(false);
  const save = useMutation({
    mutationFn: async () => {
      const publication = await result(
        client.POST("/api/setup/apply", {
          body: {
            selection: {
              model: recipe!,
              new_model_id: id,
              new_model_name: name.trim() || recipe!.route,
              environment_profile: "environment-native",
              enable_live: false,
            },
          },
        }),
      );
      if (!publication.completed)
        throw new Error(
          publication.error_message || "Couldn't finish saving the model.",
        );
    },
    onSuccess: async () => {
      await queries.invalidateQueries();
      onSaved(id);
      onClose();
    },
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
      title={
        <span ref={title} tabIndex={-1}>
          Add model
        </span>
      }
      initialFocus={title}
      disablePointerDismissal
      size="lg"
      closeLabel="Close"
      footer={
        <>
          <Button variant="outline" disabled={save.isPending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!recipe || !recipeReady}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className={styles.stack}>
        <ErrorNotice error={save.error} />
        <ModelEditor
          nameField={
            <TextField label="Model name" value={name} onChange={setName} />
          }
          value={recipe}
          onChange={setRecipe}
          onRecipeReady={setRecipeReady}
        />
      </div>
    </ModalFrame>
  );
}
