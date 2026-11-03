import type { TFunction } from "i18next";
import { errorMessage } from "../i18n/error-message";
import type { StopRequest } from "./stop-operation";
import type { Schema } from "../transport/client";

// Existing draft/submission feedback has no message code. Match only known
// application text; never translate arbitrary server diagnostics or file bytes.
export const composerMessages = new Set([
  "This chat already exists. Open it and check its settings before sending your saved input.",
  "Chat not found. Your input is saved here. Send again to retry creating the same chat.",
  "Couldn't confirm the chat was created. Send again to check.",

  "Synchronize your edits before sending.",
  "Updating chat settings…",
  "Loading chat…",
  "Your message may already be sent. Open the chat and check before sending again.",
  "Review the archived chat's outcome before sending this input again.",
  "Couldn't remove the saved draft from this browser.",
  "Couldn't save the draft. Keep this tab open to keep your input.",
  "Checking the previous creation attempt…",
  "The coordinator must be available in this project. Retry or remove the assignment.",
  "Review chat settings before sending.",
  "Connected",
  "Connecting",
  "Disconnected",
  "Server restarted",
  "Cancelled. Your input is still saved here.",
  "The shared draft changed. Review your input before sending.",
  "Couldn't sync. Your input is saved here. Send again when connected.",
  "Cannot capture input.",
  "The current operation is unavailable. Refresh its status.",
  "The run didn't accept your message. Your input is still saved here.",
  "Couldn't confirm delivery. Your message may already be sent and removed from the shared draft. Check the chat before sending again.",
  "Stopped preparing the message. Your input is still saved here.",
  "Couldn't prepare the chat. Your input is still saved here.",
  "Upload failed. Retry or remove the attachment.",
  "Select up to eight attachments.",
  "Selected attachments exceed 20 MiB.",
  "{{name}} exceeds 10 MiB.",
  "Upload failed.",
  "Binary attachment. Download to inspect its original bytes.",
  "Too large to preview. Download the file to view it.",
  "Preview unavailable.",
  "Could not load skills: {{message}}",
  "Could not load skills. Type $ again to retry.",
  "This upload is unavailable in this tab. Remove it and attach the file again.",
  "This draft is over the 512 KiB limit. Copy your text before joining a new draft.",
  "Invalid draft frame.",
  "Draft synchronization rejected.",
  "Draft synchronization failed.",
  "Requesting Stop…",
  "Stopping… Waiting for execution and cleanup to finish.",
  "This operation already ended. Checking its final status…",
  "Couldn't confirm the stop request. Checking whether the run stopped; the request hasn't been resent.",
]);

export function composerMessage(
  message: string,
  t: TFunction,
  values?: Record<string, string>,
): string {
  return composerMessages.has(message) ? t(message, values ?? {}) : message;
}

export const stopOutcomeMessages = {
  completed: "Operation completed. Refreshing chat…",
  suspended: "Operation suspended. Refreshing chat…",
  failed: "Operation failed. Refreshing chat…",
  cancelled: "Operation stopped. Refreshing chat…",
} satisfies Record<
  Exclude<Schema<"RootOperationStatus">, "preparing" | "running">,
  string
>;

export function stopMessage(request: StopRequest, t: TFunction): string {
  if (request.outcome && request.outcome in stopOutcomeMessages)
    return t(
      stopOutcomeMessages[request.outcome as keyof typeof stopOutcomeMessages],
    );
  if (request.phase === "rejected" && request.rejection !== undefined)
    return t("Stop was not accepted. {{message}}", {
      message: errorMessage(request.rejection, t),
    });
  return composerMessage(request.message, t);
}
