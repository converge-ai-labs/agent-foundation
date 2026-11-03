import type { TFunction } from "i18next";
import {
  ApiError,
  EmptyResponseError,
  NetworkError,
} from "../transport/client";

/** Translate only messages known to originate in this client. */
export function errorMessage(error: unknown, t: TFunction): string {
  if (error instanceof EmptyResponseError)
    return t("Server returned an empty response.");
  if (error instanceof NetworkError)
    return t(
      "Unable to reach the server. Check your connection and try again.",
    );
  if (error instanceof ApiError && error.localFallback)
    return error.status === 401
      ? t("Access expired. Enter the API key printed by this server.")
      : t("Request failed ({{status}}).", { status: error.status });
  return error instanceof Error ? error.message : String(error);
}
