import { useEffect, useState } from "react";
import { Button, ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

/** A retained observation, never a live desktop or an input surface. */
export function RunScreenshot({
  runId,
  imageId,
}: {
  runId: string;
  imageId: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      size="xl"
      open={open}
      onOpenChange={setOpen}
      title={t("Computer screenshot")}
      description={t(
        "This is the screen observed by the Agent at this step. It is not a live desktop.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button type="button" size="sm" variant="outline">
          {t("View screenshot")}
        </Button>
      }
    >
      {open && <ScreenshotPreview runId={runId} imageId={imageId} />}
    </ModalFrame>
  );
}

function ScreenshotPreview({
  runId,
  imageId,
}: {
  runId: string;
  imageId: string;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setUrl(undefined);
    setError(undefined);
    void client
      .workspace(workspace.id)
      .GET("/api/v1/runs/{run_id}/images/{image_id}", {
        params: { path: { run_id: runId, image_id: imageId } },
        parseAs: "blob",
        signal: controller.signal,
      })
      .then(data)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        revalidateSession(failure);
        setError(failure);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, workspace.id, runId, imageId, retry]);
  return (
    <div className="flex flex-col gap-3">
      {url ? (
        <img
          src={url}
          alt={t("Computer screenshot")}
          className="max-h-[70vh] w-full object-contain"
        />
      ) : (
        !error && <p role="status">{t("Loading screenshot…")}</p>
      )}
      {!!error && (
        <p>
          {t(
            "The screenshot may not have been saved yet. Retry after the run reaches its next checkpoint.",
          )}
        </p>
      )}
      <ErrorNotice error={error} retry={() => setRetry((value) => value + 1)} />
    </div>
  );
}
