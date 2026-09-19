import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { Link } from "react-router";
import { useProjects, useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";

/** Forget local configuration, never invoke a remote lifecycle operation. */
export function ForgetEnvironment({
  path,
  name,
  resourceId,
}: {
  path: string;
  name: string;
  resourceId: string;
}) {
  const { client } = useTransport();
  const cache = useQueryClient();
  const projects = useProjects();
  const sources = useSources();
  const references =
    projects.data?.filter(
      (project) =>
        project.defaults?.environment_profile === resourceId ||
        project.defaults?.environment_bindings?.some(
          (binding) => binding.device_id === resourceId,
        ),
    ) ?? [];
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () =>
      result(
        client.DELETE("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      ),
    onSuccess: () => {
      setOpen(false);
      void cache.invalidateQueries();
    },
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next) => {
        if (!remove.isPending) {
          setOpen(next);
          remove.reset();
        }
      }}
      title={`Forget ${name}?`}
      closeLabel="Cancel"
      description="Remove this local connection or profile configuration, even if its target no longer exists. Remote files, Devices, active Runs and captured history are not deleted."
      trigger={
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-label={`Forget ${name}`}
        >
          Forget
        </Button>
      }
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={remove.isPending}
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Forget configuration
          </Button>
        </>
      }
    >
      <p>
        Update any <Link to="/settings">General defaults</Link> or Project
        defaults that reference this resource first. Existing conversations keep
        their selections; remove or replace those selections before the next
        Run.
      </p>
      {references.length > 0 && (
        <div>
          <p>Referenced by these Projects:</p>
          <ul>
            {references.map((project) => {
              const source = sources.data?.sources.find((item) =>
                item.resource_ids.includes(project.project_id),
              );
              return (
                <li key={project.project_id}>
                  {source ? (
                    <Link
                      to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                    >
                      {project.name}
                    </Link>
                  ) : (
                    project.name
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <p className="text-sm break-all">{path}</p>
      <ErrorNotice error={remove.error} />
    </ModalFrame>
  );
}
