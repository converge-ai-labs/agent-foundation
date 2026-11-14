import { useTranslation } from "../i18n";
import { Trans } from "react-i18next";
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
  const { t } = useTranslation();
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
      bottomStickOnMobile
      open={open}
      onOpenChange={(next) => {
        if (!remove.isPending) {
          setOpen(next);
          remove.reset();
        }
      }}
      title={t("Forget {{name}}?", { name })}
      closeLabel={t("Close")}
      description={t(
        "Remove this saved connection or profile. Remote files, devices, active runs and history are kept.",
      )}
      trigger={
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-label={t("Forget {{name}}", { name })}
        >
          {t("Forget")}
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
            {t("Cancel")}
          </Button>
          <Button
            type="button"
            loading={remove.isPending}
            onClick={() => remove.mutate()}
          >
            {t("Forget")}
          </Button>
        </>
      }
    >
      <p>
        <Trans
          i18nKey="Remove this from <settings>General settings</settings> and project defaults first. Update any existing chats that still use it before their next run."
          components={{ settings: <Link to="/settings" /> }}
        />
      </p>
      {references.length > 0 && (
        <div className="grid gap-2">
          <p>{t("Used by these projects:")}</p>
          <ul className="grid gap-2 pl-4 list-disc break-words">
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
