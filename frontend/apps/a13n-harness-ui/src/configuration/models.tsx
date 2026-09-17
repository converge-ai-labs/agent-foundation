import { useContext, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { Button, ModalFrame } from "a13n-ui";
import { useSelectors, useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, PageHeader, TextField } from "../shell/ui";
import { DraftContext, DraftLinks, NewResourceButton } from "./sources";
import { ModelEditor } from "./model-editor";
import { template, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

export function ModelsPage() {
  const selectors = useSelectors();
  const sources = useSources();
  const { client } = useTransport();
  const drafts = useContext(DraftContext);
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
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
  return (
    <>
      <PageHeader
        title="Models"
        description="Reusable model connections. Only saved Models appear in conversation choices; editing one does not change active Runs or Agent tools."
        actions={<NewResourceButton kind="model" />}
      />
      <ErrorNotice error={selectors.error || sources.error || clone.error} />
      <TextField
        label="Search models"
        type="search"
        value={search}
        onChange={setSearch}
      />
      <DraftLinks kinds={["model"]} search={search} />
      <div className={styles.resourceList}>
        {(selectors.data?.models ?? [])
          .filter((model) =>
            `${model.name} ${model.route} ${model.model_id}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((model) => {
            const source = sources.data?.sources.find(
              (item) =>
                item.resource_kind === "model" &&
                item.resource_ids.includes(model.model_id),
            );
            return (
              <div key={model.model_id} className={styles.resourceRow}>
                <div>
                  {source ? (
                    <Link
                      to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                    >
                      <strong>{model.name}</strong>
                    </Link>
                  ) : (
                    <strong>{model.name}</strong>
                  )}
                  <small>{model.route}</small>
                  <small>{source?.relative_path ?? model.model_id}</small>
                </div>
                <span>{source?.writable ? "Editable" : "Read only"}</span>
                {source?.content_available &&
                  source.resource_ids.length === 1 && (
                    <Button
                      variant="outline"
                      disabled={clone.isPending}
                      onClick={() =>
                        clone.mutate({
                          path: source.relative_path,
                          name: model.name,
                        })
                      }
                    >
                      Clone
                    </Button>
                  )}
              </div>
            );
          })}
      </div>
      {selectors.data && !selectors.data.models?.length && (
        <p>
          No saved Models yet. Add a subscription or API connection to get
          started.
        </p>
      )}
    </>
  );
}

/** Add a Model without discarding the Agent draft or selecting an unsaved ID. */
export function AddModelButton({ onSaved }: { onSaved: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        Add model
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
  const { client } = useTransport();
  const queries = useQueryClient();
  const [id] = useState(() => `model-${crypto.randomUUID().slice(0, 8)}`);
  const [name, setName] = useState("");
  const [recipe, setRecipe] = useState<Schema<"ModelRecipe"> | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      let content = template("model", id);
      content = updateDocument(content, ["name"], name.trim() || recipe!.route);
      for (const [key, value] of Object.entries(recipe!))
        content = updateDocument(content, [key], value);
      await result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: `models/${id}.yaml` } },
          body: { content },
        }),
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
      title="Add model"
      description="Save a reusable Model, then select it for this Agent. No model request will be sent."
      closeLabel="Cancel"
      footer={
        <Button
          disabled={!recipe}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save model & select
        </Button>
      }
    >
      <div className={styles.stack}>
        <ErrorNotice error={save.error} />
        <TextField label="Model name" value={name} onChange={setName} />
        <ModelEditor value={recipe} onChange={setRecipe} />
      </div>
    </ModalFrame>
  );
}
