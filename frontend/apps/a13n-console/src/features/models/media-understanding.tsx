import { SettingsSection } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { representation } from "../../shared/api";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import { mediaDefaultsQuery } from "./api";
import {
  MediaUnderstandingFields,
  type MediaKind,
  type MediaSelection,
} from "./media-understanding-fields";
import styles from "./models.module.css";

/**
 * The last level of the Run override → Agent → Workspace precedence: the
 * Models that read media for Agents whose own Model cannot. Each row is an
 * independent setting, so a change replaces the whole saved selection under
 * the current ETag on its own, without a draft to keep or discard.
 */
export function MediaUnderstandingDefaults() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const defaults = mediaDefaultsQuery(client, workspace.id);
  const query = useQuery(defaults);
  const save = useMutation({
    mutationFn: (change: {
      kind: MediaKind;
      etag: string;
      selection: MediaSelection;
    }) =>
      client.http
        .PUT("/api/v1/workspaces/{workspace}/media-understanding-defaults", {
          params: {
            path: { workspace: workspace.id },
            header: { "If-Match": change.etag },
          },
          body: change.selection,
        })
        .then(representation),
    onSuccess: (result) => cache.setQueryData(defaults.queryKey, result),
    onError: (error) => {
      // Nothing local is worth keeping: reload and show what is actually saved.
      if (error instanceof ApiError && error.status === 412)
        void query.refetch();
    },
  });
  const inFlight = save.isPending ? save.variables : undefined;
  // One save at a time, so the row in flight already carries the whole selection.
  const value: MediaSelection | undefined =
    inFlight?.selection ?? query.data?.value;
  const error = query.error ?? save.error;
  const conflict =
    error === save.error && error instanceof ApiError && error.status === 412;
  return (
    <div className={styles.mediaDefaults}>
      <p className={styles.mediaRule}>
        {t(
          "An agent whose own model reads a kind never uses these; an agent or a run can pick a different model.",
        )}
      </p>
      <ErrorNotice
        error={error}
        description={
          conflict
            ? t(
                "These defaults changed elsewhere. The saved selection is shown again; choose once more to apply your change.",
              )
            : undefined
        }
      />
      {!value ? (
        <InlineLoading />
      ) : (
        <SettingsSection>
          <MediaUnderstandingFields
            value={value}
            inherit={{ label: t("Not configured") }}
            disabled={
              !can("models.manage") || !query.data?.etag || save.isPending
            }
            pendingKind={inFlight?.kind}
            onChange={(selection, kind) => {
              const etag = query.data?.etag;
              if (etag) save.mutate({ kind, etag, selection });
            }}
          />
        </SettingsSection>
      )}
    </div>
  );
}
