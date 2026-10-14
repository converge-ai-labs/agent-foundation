import { Button, ModalFrame } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import styles from "./providers.module.css";

/** The preview explains references; the DELETE transaction remains authoritative. */
export function EnvironmentConnectionDelete({
  provider,
  etag,
  disabled,
  onDeleted,
}: {
  provider: Schema["Provider"];
  etag?: string;
  disabled?: boolean;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  const http = useClient().workspace(workspace.id);
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const params = { path: { provider_id: provider.id } };
  const refs = useQuery({
    queryKey: ["environment-provider-references", workspace.id, provider.id],
    enabled: open,
    queryFn: ({ signal }) =>
      http
        .GET("/api/v1/environment-providers/{provider_id}/references", {
          params,
          signal,
        })
        .then(data),
  });
  const blocked =
    !refs.data || refs.data.template_count + refs.data.environment_count > 0;
  const deletion = useMutation({
    mutationFn: () =>
      http.DELETE("/api/v1/environment-providers/{provider_id}", {
        params,
        headers: ifMatch(etag),
      }),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environment-providers"] });
      void cache.invalidateQueries({
        queryKey: ["environment-provider-options"],
      });
      onDeleted();
    },
    onError: () => {
      void refs.refetch();
    },
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (!deletion.isPending) {
          setOpen(value);
          deletion.reset();
        }
      }}
      title={t("Delete connection")}
      description={provider.name}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button type="button" variant="ghost" disabled={disabled}>
          {t("Delete connection")}
        </Button>
      }
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={deletion.isPending}
            onClick={() => setOpen(false)}
          >
            {t("Cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            loading={deletion.isPending}
            disabled={blocked || refs.isFetching || !!refs.error}
            onClick={() => deletion.mutate()}
          >
            {t("Delete connection")}
          </Button>
        </>
      }
    >
      {refs.isPending ? (
        <Loading variant="form" rows={2} />
      ) : (
        refs.data && (
          <>
            <p>
              {t(
                blocked
                  ? "This connection is in use and cannot be deleted. Disabled templates and historical environments also count as references."
                  : "This connection has no references. Deleting it permanently removes its configuration and saved credentials.",
              )}
            </p>
            <ReferenceList
              label={t("Templates")}
              count={refs.data.template_count}
              items={refs.data.templates}
            />
            <ReferenceList
              label={t("Environments")}
              count={refs.data.environment_count}
              items={refs.data.environments}
            />
          </>
        )
      )}
      <ErrorNotice error={refs.error ?? deletion.error} />
      <Button
        type="button"
        variant="outline"
        disabled={refs.isFetching || deletion.isPending}
        onClick={() => void refs.refetch()}
      >
        {t("Refresh references")}
      </Button>
    </ModalFrame>
  );
}

function ReferenceList({
  label,
  count,
  items,
}: {
  label: string;
  count: number;
  items: Schema["ProviderReference"][];
}) {
  const { t } = useTranslation();
  if (!count) return null;
  return (
    <section className={styles.referenceSection}>
      <h3>
        {label} · {count}
      </h3>
      <ul className={styles.referenceList}>
        {items.map((item) => (
          <li key={item.id}>
            <span>
              {item.name}
              <small>{item.id}</small>
            </span>
            <StatePill state={item.status} />
          </li>
        ))}
      </ul>
      {count > items.length && (
        <p>
          {t("Showing the first {{count}} references.", {
            count: items.length,
          })}
        </p>
      )}
    </section>
  );
}
