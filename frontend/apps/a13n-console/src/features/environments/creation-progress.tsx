import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./environments.module.css";

export const FIRST_DESKTOP_NOTICE =
  "The first desktop creation usually takes 1–2 minutes to prepare a private template; it may take longer. Later creations reuse it. You can leave this page while creation continues.";

export const MODAL_FIRST_DESKTOP_NOTICE =
  "The first Modal desktop creation builds an image and can take several minutes. Later creations reuse it. You can leave this page while creation continues.";

export const DAYTONA_FIRST_DESKTOP_NOTICE =
  "The first Daytona desktop creation builds a snapshot and can take several minutes. Later creations reuse it. You can leave this page while creation continues.";

export const VERCEL_FIRST_DESKTOP_NOTICE =
  "The first Vercel desktop creation builds a snapshot and can take several minutes. Later creations reuse it. You can leave this page while creation continues.";

export const SPRITES_DESKTOP_NOTICE =
  "Each new Sprites desktop installs Envd and desktop dependencies and can take several minutes. Existing Sprites retain the installation after sleep. You can leave this page while creation continues.";

const STAGES = {
  preparing: "Preparing desktop template",
  building: "Building desktop template in E2B",
  starting: "Starting and checking desktop",
  ready: "Desktop is ready",
  failed: "Desktop creation needs attention",
  inactive: "Desktop is not running",
} as const;

export function CreationProgress({
  environment,
  etag,
  providerType,
}: {
  environment: Schema["EnvironmentView"];
  etag?: string;
  providerType?: string;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation();
  const [clock, setClock] = useState(Date.now);
  const progress = useQuery({
    queryKey: ["environment-creation", environment.id],
    queryFn: ({ signal }) =>
      client
        .workspace(environment.workspace_id)
        .GET("/api/v1/environments/{environment_id}/creation", {
          params: { path: { environment_id: environment.id } },
          signal,
        })
        .then(data),
    refetchInterval: environment.status === "creating" ? 2000 : false,
  });
  useEffect(() => {
    if (environment.status !== "creating" || progress.data?.ended_at) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [environment.status, progress.data?.ended_at]);
  const retry = useMutation({
    mutationFn: () =>
      client
        .workspace(environment.workspace_id)
        .POST("/api/v1/environments/{environment_id}/retry-creation", {
          params: { path: { environment_id: environment.id } },
          headers: ifMatch(etag),
        })
        .then(data),
    onSuccess: async () => {
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["environment", environment.id] }),
        cache.invalidateQueries({
          queryKey: ["environment-creation", environment.id],
        }),
        cache.invalidateQueries({ queryKey: ["environments"] }),
      ]);
    },
  });
  const stage =
    environment.status === "ready"
      ? "ready"
      : (progress.data?.stage ?? "preparing");
  const start =
    progress.data?.started_at ??
    environment.operation_started_at ??
    environment.created_at;
  const end = progress.data?.ended_at
    ? Date.parse(progress.data.ended_at)
    : progress.data
      ? Date.parse(progress.data.observed_at) +
        Math.max(0, clock - progress.dataUpdatedAt)
      : clock;
  const seconds = Math.max(0, Math.floor((end - Date.parse(start)) / 1000));
  return (
    <section className={styles.factGroup}>
      <h3 aria-live="polite">
        {t(
          providerType === "sprites" && stage === "preparing"
            ? "Allocating Sprite"
            : providerType === "sprites" && stage === "building"
              ? "Installing Envd and desktop dependencies"
              : stage === "building" && providerType === "modal"
                ? "Building desktop image in Modal"
                : stage === "building" && providerType === "daytona"
                  ? "Building desktop snapshot in Daytona"
                  : stage === "building" && providerType === "vercel"
                    ? "Building desktop snapshot in Vercel"
                    : STAGES[stage],
        )}
      </h3>
      {environment.status === "creating" && (
        <>
          <p>
            {t("Elapsed: {{minutes}}m {{seconds}}s", {
              minutes: Math.floor(seconds / 60),
              seconds: seconds % 60,
            })}
          </p>
          {stage !== "failed" && (
            <p>
              {t(
                "Creation continues in the background. You can leave this page and return to Environment instances to check progress.",
              )}
            </p>
          )}
          {environment.failure && (
            <p role="alert">{environment.failure.message}</p>
          )}
          {progress.data?.retryable && can("write") && can("run") ? (
            <Button
              type="button"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
            >
              {t("Retry creation")}
            </Button>
          ) : (
            environment.failure && (
              <p>
                {t(
                  "The previous operation is still being reconciled. Retry becomes available once its outcome is confirmed.",
                )}
              </p>
            )
          )}
        </>
      )}
      <ErrorNotice
        error={progress.error}
        retry={() => void progress.refetch()}
      />
      <ErrorNotice error={retry.error} retry={() => void progress.refetch()} />
    </section>
  );
}

/** A snapshot carries no control grant, is never cached, and is released when this panel closes. */
export function DesktopPreview({
  environment,
}: {
  environment: Schema["EnvironmentView"];
}) {
  const client = useClient(),
    { t } = useTranslation();
  const [url, setUrl] = useState<string>();
  const [image, setImage] = useState<Blob>();
  const preview = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const response = await client
        .workspace(environment.workspace_id)
        .GET("/api/v1/environments/{environment_id}/desktop-preview", {
          params: { path: { environment_id: environment.id } },
          parseAs: "blob",
        });
      return data(response);
    },
    onSuccess: setImage,
  });
  useEffect(() => {
    if (!image) return;
    const objectUrl = URL.createObjectURL(image);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [image]);
  return (
    <section className={styles.factGroup}>
      <Button
        type="button"
        disabled={preview.isPending}
        onClick={() => preview.mutate()}
      >
        {t(url ? "Refresh desktop preview" : "View desktop")}
      </Button>
      {preview.isPending && (
        <p role="status">{t("Loading desktop preview…")}</p>
      )}
      <ErrorNotice error={preview.error} />
      {url && (
        <>
          <p>
            {t(
              "Read-only snapshot. Refresh to see changes; use a session for interactive desktop control.",
            )}
          </p>
          <img
            src={url}
            alt={t("Desktop preview")}
            style={{ width: "100%", borderRadius: 8 }}
          />
        </>
      )}
    </section>
  );
}
