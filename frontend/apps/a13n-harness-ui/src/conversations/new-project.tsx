import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { template, updateDocument } from "../configuration/documents";
import { ErrorNotice, TextField } from "../shell/ui";
import { ProjectFolders } from "../configuration/project-folders";
import styles from "./conversation.module.css";

export function NewProject({
  close,
  created,
}: {
  close: () => void;
  created: (id: string) => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [id] = useState(() => `project-${crypto.randomUUID()}`);
  const [name, setName] = useState("");
  const [roots, setRoots] = useState([{ path: "" }]);
  const validRoots =
    roots.length > 0 && roots.every((root) => root.path.trim());
  const save = useMutation({
    mutationFn: () => {
      let content = updateDocument(
        template("project", id),
        ["name"],
        name.trim(),
      );
      content = updateDocument(
        content,
        ["roots"],
        roots.map((root) => ({ path: root.path.trim() })),
      );
      // The existing source publication validates the whole configuration before saving.
      return result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: `projects/${id}.yaml` } },
          body: { content },
        }),
      );
    },
    onSuccess: () => {
      void queries.invalidateQueries();
      created(id);
      close();
    },
  });
  return (
    <ModalFrame
      open
      title="Add project"
      description="Give this workspace a name and a directory on the server. Adding a project does not create a conversation."
      closeLabel="Close"
      onOpenChange={(open) => {
        if (!open && !save.isPending) close();
      }}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() && validRoots && !save.isPending) save.mutate();
        }}
      >
        <TextField label="Project name" value={name} onChange={setName} />
        <ProjectFolders roots={roots} onChange={setRoots} />
        <small>
          Agent, Environment, and other defaults can be changed in Project
          settings after saving.
        </small>
        <ErrorNotice error={save.error} />
        <Button
          type="submit"
          loading={save.isPending}
          disabled={!name.trim() || !validRoots}
        >
          Add project
        </Button>
      </form>
    </ModalFrame>
  );
}
