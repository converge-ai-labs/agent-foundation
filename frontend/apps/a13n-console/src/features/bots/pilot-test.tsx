import { Button } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import { TestObservation } from "./test-observation";
import { Step } from "./wizard";
import styles from "./connect.module.css";

export function PilotTest({
  account,
  back,
}: {
  account: Schema["Account"];
  back: () => void;
}) {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    cache = useQueryClient();
  const key = useIdempotency();
  const observationKey = ["bot-setup-test", workspace.id, account.id];
  const observation = useQuery({
    queryKey: observationKey,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/tests/latest", {
          params: { path: { account_id: account.id } },
          signal,
        })
        .then(data),
  });
  const targets = useQuery({
    queryKey: ["account-targets", workspace.id, account.id, "setup-test"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/application-accounts/{account_id}/targets", {
            params: {
              path: { account_id: account.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const enabled =
    targets.data?.filter(
      (item) =>
        item.receive_enabled &&
        item.target_kind ===
          (account.provider_key === "github" ? "repository" : "conversation"),
    ) ?? [];
  const target = enabled.length === 1 ? enabled[0] : undefined;
  const create = useMutation({
    mutationFn: (body: Schema["CreateBotTest"]) =>
      client.http
        .POST("/api/v1/application-accounts/{account_id}/bot/tests", {
          params: {
            path: { account_id: account.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data),
    onSuccess: (result) => {
      cache.setQueryData(observationKey, { latest: result });
      key.reset();
    },
  });
  const test = observation.data?.latest;
  const prepare = (
    <Button
      className={styles.prepareTest}
      loading={create.isPending}
      disabled={
        create.isPending ||
        !account.receive_enabled ||
        !target ||
        !!targets.error
      }
      onClick={() => {
        if (target)
          create.mutate(
            create.isError && create.variables
              ? create.variables
              : {
                  expected_version: account.version,
                  target_id: target.id,
                  target_version: target.version,
                },
          );
      }}
    >
      {create.isError ? t("Retry preparing test") : t("Prepare a test message")}
    </Button>
  );
  return (
    <Step
      title={t(
        account.provider_key === "github"
          ? "Test in your pilot repository"
          : "Test in your pilot conversation",
      )}
      description={t(
        "Prepare a test message, then send it yourself in the pilot conversation. Sending it invokes your agent and may consume model usage.",
      )}
      onBack={back}
      backLabel={t("Review reception settings")}
      primary={
        <>
          {prepare}
          <Button
            variant="outline"
            render={<Link to={`${basePath}/bots/${account.id}`} />}
          >
            {t("Open bot")}
          </Button>
        </>
      }
    >
      {account.provider_key === "github" && (
        <p className={styles.hint}>
          {t(
            "Post the test text as an Issue or PR comment in the pilot repository. For polling, mention the connected GitHub account and allow at least one polling interval. Use an allowed sender account.",
          )}
        </p>
      )}
      <ErrorNotice error={targets.error} retry={() => void targets.refetch()} />
      <ErrorNotice
        error={observation.error}
        retry={() => void observation.refetch()}
      />
      <ErrorNotice error={create.error} />
      {targets.isPending || observation.isPending ? (
        <Loading variant="list" rows={3} />
      ) : (
        <>
          {!account.receive_enabled && (
            <p className={styles.hint} role="status">
              {t(
                "Reception is still disabled. Enable it before sending the test message.",
              )}
            </p>
          )}
          {!target && (
            <p className={styles.hint} role="status">
              {t(
                "Configure exactly one pilot conversation before preparing a test.",
              )}
            </p>
          )}
          {test && !observation.error && (
            <TestObservation
              account={account}
              test={test}
              showMessage
              refreshing={observation.isFetching}
              refresh={() => void observation.refetch()}
            />
          )}
        </>
      )}
      <p className={styles.hint}>
        {t(
          "Closing setup does not cancel work that has already been accepted.",
        )}
      </p>
    </Step>
  );
}
