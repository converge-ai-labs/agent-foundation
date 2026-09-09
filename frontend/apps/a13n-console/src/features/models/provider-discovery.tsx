import { useMutation } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice, StateBadge } from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { ModelEditor } from "./model-editor";

export function ProviderTest({
  scope,
  providerId,
}: {
  scope: ModelScope;
  providerId: string;
}) {
  const api = modelApi(useClient(), scope),
    { t } = useTranslation();
  const test = useMutation({ mutationFn: () => api.testProvider(providerId) });
  return (
    <ModalFrame
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Test")}
        </Button>
      }
      size={"md"}
      title={t("Test provider")}
      description={t(
        "This contacts the provider and may consume quota or incur cost.",
      )}
      closeLabel={t("Close")}
    >
      <Button
        variant="outline"
        loading={test.isPending}
        onClick={() => test.mutate()}
        type="button"
      >
        {t("Run connection test")}
      </Button>
      <ErrorNotice error={test.error} />
      {test.data && (
        <p role="status">
          <StateBadge state={test.data.success ? "succeeded" : "failed"} />{" "}
          {test.data.message} · {test.data.elapsed_ms} ms
        </p>
      )}
    </ModalFrame>
  );
}

export function Discovery({
  scope,
  provider,
}: {
  scope: ModelScope;
  provider: Schema["ModelProvider"];
}) {
  const api = modelApi(useClient(), scope),
    { t } = useTranslation(),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(0);
  const discover = useMutation({ mutationFn: () => api.discover(provider.id) });
  const candidates =
    discover.data?.items.filter((item) =>
      `${item.upstream_model} ${item.display_name}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) ?? [];
  return (
    <ModalFrame
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Discover")}
        </Button>
      }
      size={"md"}
      title={t("Discover models")}
      description={t(
        "Candidates are suggestions. Add a model to make it available to agents.",
      )}
      closeLabel={t("Close")}
    >
      <div className={styles.stack}>
        <Button
          variant="outline"
          loading={discover.isPending}
          onClick={() => {
            setPage(0);
            discover.mutate();
          }}
          type="button"
        >
          {t("Refresh catalog")}
        </Button>
        <ErrorNotice error={discover.error} />
        {discover.data && (
          <>
            <FormField className="min-w-0 w-full" label={t("Search models")}>
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
              />
            </FormField>
            {candidates.slice(page * 10, page * 10 + 10).map((candidate) => (
              <div key={candidate.upstream_model} className={styles.toolbar}>
                <span>
                  {candidate.display_name ?? candidate.upstream_model}
                  <small className={styles.muted}>
                    {candidate.upstream_model}
                  </small>
                </span>
                <ModelEditor
                  scope={scope}
                  candidate={candidate}
                  providerId={provider.id}
                />
              </div>
            ))}
            {!candidates.length && (
              <p>{t("No models found. You can still add a model manually.")}</p>
            )}
            <div className={styles.pagination}>
              <Button
                variant="outline"
                disabled={!page}
                onClick={() => setPage(page - 1)}
                type="button"
              >
                {t("Previous")}
              </Button>
              <Button
                variant="outline"
                disabled={(page + 1) * 10 >= candidates.length}
                onClick={() => setPage(page + 1)}
                type="button"
              >
                {t("Next")}
              </Button>
            </div>
          </>
        )}
      </div>
    </ModalFrame>
  );
}
