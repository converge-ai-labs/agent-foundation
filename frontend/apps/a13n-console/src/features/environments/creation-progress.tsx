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
  "Envd is installed directly on each persistent Vercel sandbox. Desktop mode also installs desktop dependencies. Creation can take several minutes; you can leave this page while it continues.";

export const SPRITES_DESKTOP_NOTICE =
  "Each new Sprites desktop installs Envd and desktop dependencies and can take several minutes. Existing Sprites retain the installation after sleep. You can leave this page while creation continues.";

export const HEADLESS_INSTALL_NOTICE =
  "Envd is installed on each new sandbox without a desktop or guest Python runtime. Creation can take several minutes; you can leave this page while it continues.";

export const HEADLESS_IMAGE_NOTICE =
  "The first headless creation builds an Envd image. Later creations reuse it. No desktop or guest Python runtime is required.";

export const HEADLESS_SNAPSHOT_NOTICE =
  "The first headless creation builds an Envd snapshot. Later creations reuse it. No desktop or guest Python runtime is required.";

export function creationNotice(
  providerType: string | undefined,
  recipe: Record<string, unknown> | undefined,
): string | undefined {
  if (
    !recipe ||
    recipe.envd_management === "preinstalled" ||
    !["envd_http", "envd_ws"].includes(String(recipe.execution))
  )
    return;
  const desktop = recipe.desktop === true;
  if (providerType === "modal" && recipe.image === "auto")
    return desktop ? MODAL_FIRST_DESKTOP_NOTICE : HEADLESS_IMAGE_NOTICE;
  if (providerType === "daytona" && recipe.snapshot === "auto")
    return desktop ? DAYTONA_FIRST_DESKTOP_NOTICE : HEADLESS_SNAPSHOT_NOTICE;
  if (
    providerType === "vercel" &&
    (!recipe.snapshot || recipe.snapshot === "auto")
  )
    return VERCEL_FIRST_DESKTOP_NOTICE;
  if (providerType === "sprites")
    return desktop ? SPRITES_DESKTOP_NOTICE : HEADLESS_INSTALL_NOTICE;
  if (providerType === "e2b") {
    if (recipe.template === "auto" && desktop) return FIRST_DESKTOP_NOTICE;
    if (
      !desktop &&
      (recipe.preparation === "headless" ||
        (recipe.preparation === undefined &&
          (!recipe.template || recipe.template === "base")))
    )
      return HEADLESS_INSTALL_NOTICE;
  }
}

const HEADLESS_STAGES = {
  preparing: "Preparing Envd environment",
  building: "Preparing headless Envd",
  starting: "Starting and checking Envd",
  ready: "Envd environment is ready",
  failed: "Environment creation needs attention",
  inactive: "Envd environment is not running",
} as const;

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
    refetchInterval: ["creating", "starting"].includes(environment.status)
      ? 2000
      : false,
  });
  useEffect(() => {
    if (
      !["creating", "starting"].includes(environment.status) ||
      !progress.data ||
      progress.data.ended_at
    )
      return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [environment.status, progress.data?.ended_at, !!progress.data]);
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
  // The server reads the instance's frozen recipe; current template edits cannot
  // enable automatic preparation or change its captured profile.
  if (!progress.data && !progress.error) return null;
  const stage =
    environment.status === "ready"
      ? "ready"
      : (progress.data?.stage ?? "preparing");
  const desktop = progress.data?.profile === "desktop";
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
          environment.status === "starting"
            ? stage === "failed"
              ? "Environment preparation needs attention"
              : "Starting and checking Envd"
            : providerType === "sprites" && stage === "preparing"
              ? "Allocating Sprite"
              : stage === "building" &&
                  (providerType === "sprites" ||
                    providerType === "vercel" ||
                    (providerType === "e2b" && !desktop))
                ? desktop
                  ? "Installing Envd and desktop dependencies"
                  : "Installing headless Envd"
                : stage === "building" && providerType === "modal"
                  ? desktop
                    ? "Building desktop image in Modal"
                    : "Building headless Envd image in Modal"
                  : stage === "building" && providerType === "daytona"
                    ? desktop
                      ? "Building desktop snapshot in Daytona"
                      : "Building headless Envd snapshot in Daytona"
                    : desktop
                      ? STAGES[stage]
                      : HEADLESS_STAGES[stage],
        )}
      </h3>
      {["creating", "starting"].includes(environment.status) && (
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
                environment.status === "starting"
                  ? "Preparation continues in the background. You can leave this page and return to check progress."
                  : "Creation continues in the background. You can leave this page and return to Environment instances to check progress.",
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
              {t(
                environment.status === "starting"
                  ? "Retry preparation"
                  : "Retry creation",
              )}
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
        disabled={
          preview.isPending || environment.desktop_preview !== "supported"
        }
        onClick={() => preview.mutate()}
      >
        {t(url ? "Refresh desktop preview" : "View desktop")}
      </Button>
      {environment.desktop_preview === "connection_unsupported" && (
        <p>{t("Desktop preview is not supported for this connection mode.")}</p>
      )}
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
