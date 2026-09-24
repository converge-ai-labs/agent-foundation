import { useQuery } from "@tanstack/react-query";
import { ReadOnlyField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import { ErrorNotice } from "../../shared/feedback";

/** The Service's OAuth callback URL, which a provider's OAuth app must allow exactly. */
export function CallbackUrlField({ description }: { description: string }) {
  const client = useClient(),
    { t } = useTranslation();
  const redirect = useQuery({
    queryKey: ["connections", "redirect-uri"],
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connections/redirect-uri", { signal })
        .then(data),
  });
  return (
    <>
      {redirect.data && (
        <ReadOnlyField label={t("Callback URL")} description={description}>
          <span className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 break-all">
              {redirect.data.redirect_uri}
            </span>
            <CopyButton
              value={redirect.data.redirect_uri}
              copyLabel={t("Copy callback URL")}
            />
          </span>
        </ReadOnlyField>
      )}
      <ErrorNotice
        error={redirect.error}
        retry={() => void redirect.refetch()}
      />
    </>
  );
}
