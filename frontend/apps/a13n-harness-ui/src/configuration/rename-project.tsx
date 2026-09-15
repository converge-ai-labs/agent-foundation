import { useContext, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import { DraftContext } from "./sources";
import styles from "../shell/workbench.module.css";

export function RenameProject({
  projectId,
  name: originalName,
  close,
}: {
  projectId: string;
  name: string;
  close: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const drafts = useContext(DraftContext);
  const sources = useSources();
  const path = sources.data?.sources.find(
    (source) =>
      source.resource_kind === "project" &&
      source.resource_ids.includes(projectId),
  )?.relative_path;
  const [name, setName] = useState(originalName);
  const source = useQuery({
    queryKey: ["source", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path! } },
          signal,
        }),
      ),
    enabled: !!path,
  });
  const draft = path ? drafts.get(path) : undefined;
  const dirty = !!draft && draft.content !== draft.base;
  const editable =
    !!source.data?.writable && !!source.data.content_available && !dirty;
  const save = useMutation({
    mutationFn: async () => {
      if (!path || !editable)
        throw new Error("This project cannot be renamed here.");
      // Read fresh source so renaming does not replay unrelated fields from an old query.
      const latest = await result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      );
      const document = readDocument(latest.content ?? "");
      if (
        !latest.writable ||
        !latest.content_available ||
        !document ||
        document.get("id") !== projectId ||
        document.get("kind") !== "project"
      )
        throw new Error(
          "Project source changed or is unavailable. Open Project settings to review it.",
        );
      const content = updateDocument(latest.content!, ["name"], name.trim());
      const publication = await result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          body: { content },
        }),
      );
      return { ...latest, content, source_digest: publication.source_digest };
    },
    onSuccess: (saved) => {
      if (path) {
        const current = drafts.get(path);
        if (!current || current.content === current.base) {
          drafts.set(path, {
            content: saved.content,
            base: saved.content,
            digest: saved.source_digest,
            replacement: false,
          });
        }
        queries.setQueryData(["source", path], saved);
      }
      void queries.invalidateQueries();
      close();
    },
  });
  return (
    <ModalFrame
      open
      title="Rename project"
      description="Change the display name without changing folders, project identity, or conversations."
      closeLabel="Close"
      onOpenChange={(open) => {
        if (!open && !save.isPending) close();
      }}
    >
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault();
          if (editable && name.trim() && !save.isPending) save.mutate();
        }}
      >
        <TextField label="Project name" value={name} onChange={setName} />
        <ErrorNotice
          error={sources.error || source.error || save.error}
          retry={
            save.error
              ? undefined
              : () => {
                  void sources.refetch();
                  if (path) void source.refetch();
                }
          }
        />
        {(sources.isPending || (path && source.isPending)) && (
          <p role="status">Reading project…</p>
        )}
        {dirty && (
          <p>
            There are unsaved changes in Project settings. Save or discard those
            changes before renaming here.
          </p>
        )}
        {sources.isSuccess &&
          (!path || (source.isSuccess && !editable && !dirty)) && (
            <p>This project source is unavailable or read only.</p>
          )}
        <div className={styles.actions}>
          <Button
            type="submit"
            loading={save.isPending}
            disabled={
              !editable ||
              !name.trim() ||
              name.trim() === originalName ||
              name.trim().length > 256
            }
          >
            Save name
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={save.isPending}
            onClick={close}
          >
            Cancel
          </Button>
          {!save.isPending && (
            <Link
              to={`/projects/${encodeURIComponent(projectId)}`}
              onClick={close}
            >
              Project settings
            </Link>
          )}
        </div>
      </form>
    </ModalFrame>
  );
}
