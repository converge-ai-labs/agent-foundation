import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useSelectors, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { template, updateDocument } from "../configuration/documents";
import { ErrorNotice } from "../shell/ui";
import {
  SettingsSection,
  SettingsTextField as TextField,
} from "../shell/settings";
import {
  EnvironmentsEditor,
  invalidEnvironments,
  type EnvironmentSelection,
} from "../configuration/environments";
import layout from "../shell/workbench.module.css";

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
  const selectors = useSelectors();
  const [environments, setEnvironments] = useState<EnvironmentSelection>({
    local_roots: [],
    environment_bindings: [],
  });
  const validRoots = !invalidEnvironments(environments, true);
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
        (environments.local_roots ?? []).map((path) => ({ path: path.trim() })),
      );
      content = updateDocument(
        content,
        ["defaults", "environment_profile"],
        environments.environment_profile_id || undefined,
      );
      content = updateDocument(
        content,
        ["defaults", "environment_bindings"],
        environments.environment_bindings,
      );
      content = updateDocument(
        content,
        ["defaults", "default_environment"],
        environments.default_environment || undefined,
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
      size="lg"
      description="Give your project a name and choose where its agents work."
      closeLabel="Close"
      onOpenChange={(open) => {
        if (!open && !save.isPending) close();
      }}
    >
      <form
        className={layout.configurationForm}
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() && validRoots && !save.isPending) save.mutate();
        }}
      >
        <SettingsSection>
          <TextField label="Project name" value={name} onChange={setName} />
        </SettingsSection>
        <SettingsSection title="Environments">
          <div className={layout.fieldGroup}>
            <EnvironmentsEditor
              requireWorkspace
              value={environments}
              profiles={selectors.data?.environments}
              onChange={(patch) =>
                setEnvironments({ ...environments, ...patch })
              }
            />
          </div>
        </SettingsSection>
        <small className="text-muted-foreground">
          Change the agent and other defaults in project settings later.
        </small>
        <ErrorNotice error={save.error} />
        <div data-a13n-form-actions className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={close}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            loading={save.isPending}
            disabled={!name.trim() || !validRoots}
          >
            Add project
          </Button>
        </div>
      </form>
    </ModalFrame>
  );
}
