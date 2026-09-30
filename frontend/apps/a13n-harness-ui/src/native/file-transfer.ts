import { result, type Transport } from "../transport/client";

export function fileTransfer(
  transport: Transport,
  path: string,
  revision: string,
  disposition: "attachment" | "inline",
  signal?: AbortSignal,
) {
  return result(
    transport.client.POST("/api/host/files/transfers", {
      body: { path, expected_revision: revision, disposition },
      signal,
    }),
  );
}

/** Let the browser save streamed bytes without collecting a whole-file Blob. */
export function downloadFile(url: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.rel = "noreferrer";
  anchor.referrerPolicy = "no-referrer";
  anchor.download = "";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}
