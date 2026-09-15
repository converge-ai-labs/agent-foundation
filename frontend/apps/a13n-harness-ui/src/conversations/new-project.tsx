import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { template, updateDocument } from "../configuration/documents";
import { ErrorNotice, TextField } from "../shell/ui";
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
  const [directory, setDirectory] = useState("");
  const [roots, setRoots] = useState<string[]>([]);
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
        [directory, ...roots]
          .filter((path) => path.trim())
          .map((path) => ({ path: path.trim() })),
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
          if (name.trim() && directory.trim() && !save.isPending) save.mutate();
        }}
      >
        <TextField label="Project name" value={name} onChange={setName} />
        <TextField
          label="Server directory"
          value={directory}
          onChange={setDirectory}
          description="A path on the server or container running Harness UI, not on this browser's computer."
        />
        <details>
          <summary>Additional roots</summary>
          <div className={styles.form}>
            {roots.map((root, index) => (
              <div key={index}>
                <TextField
                  label={`Additional server directory ${index + 1}`}
                  value={root}
                  onChange={(value) =>
                    setRoots((current) =>
                      current.map((item, i) => (i === index ? value : item)),
                    )
                  }
                />
                <Button
                  variant="ghost"
                  onClick={() =>
                    setRoots((current) => current.filter((_, i) => i !== index))
                  }
                >
                  Remove directory {index + 1}
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              onClick={() => setRoots((current) => [...current, ""])}
            >
              Add another directory
            </Button>
            <small>
              Agent, Environment, and other defaults can be changed in Project
              settings after saving.
            </small>
          </div>
        </details>
        <ErrorNotice error={save.error} />
        <Button
          type="submit"
          loading={save.isPending}
          disabled={!name.trim() || !directory.trim()}
        >
          Add project
        </Button>
      </form>
    </ModalFrame>
  );
}
