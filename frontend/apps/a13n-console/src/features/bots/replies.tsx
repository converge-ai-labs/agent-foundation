import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Empty, Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import { ReplyPill } from "../integrations/platform";
import styles from "./bots.module.css";

export function BotReplies({
  accountId,
  runId,
}: {
  accountId: string;
  runId: string;
}) {
  const { can } = useWorkspace(),
    { t } = useTranslation();
  if (!can("application_account.read"))
    return (
      <p className={styles.hint}>
        {t("Reply observations require application account read access.")}
      </p>
    );
  return (
    <ReplyList
      key={`${accountId}:${runId}`}
      accountId={accountId}
      runId={runId}
    />
  );
}

function ReplyList({ accountId, runId }: { accountId: string; runId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["bot-replies", workspace.id, accountId, runId, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/replies", {
          params: {
            path: { account_id: accountId },
            query: { run_id: runId, cursor: page.cursor, limit: 20 },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.replyPanel}>
      <div className={styles.replyPanelHead}>
        <CopyableId value={runId} />
        <Button
          size="sm"
          variant="ghost"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <ArrowClockwiseIcon aria-hidden="true" />
          {t("Refresh")}
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="list" rows={2} />
      ) : query.data?.items.length ? (
        <>
          <ol className={styles.replyList}>
            {query.data.items.map((item) => (
              <li key={item.id}>
                <ReplyPill status={item.status} />
                <dl className={styles.facts} data-dense="true">
                  <div>
                    <dt>{t("Started")}</dt>
                    <dd>
                      <Timestamp value={item.started_at} />
                    </dd>
                  </div>
                  {item.finished_at && (
                    <div>
                      <dt>{t("Observed")}</dt>
                      <dd>
                        <Timestamp value={item.finished_at} />
                      </dd>
                    </div>
                  )}
                  <div>
                    <dt>{t("Attempt")}</dt>
                    <dd>
                      <CopyableId value={item.run_attempt_id} />
                    </dd>
                  </div>
                  {item.receipt && (
                    <>
                      <div>
                        <dt>{t("Message ID")}</dt>
                        <dd>
                          <CopyableId
                            value={String(
                              "message_ts" in item.receipt
                                ? item.receipt.message_ts
                                : "comment_id" in item.receipt
                                  ? item.receipt.comment_id
                                  : item.receipt.message_id,
                            )}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>{t("Request ID")}</dt>
                        <dd>
                          <CopyableId value={item.receipt.request_id} />
                        </dd>
                      </div>
                    </>
                  )}
                  {item.error_code && (
                    <div>
                      <dt>{t("Reason code")}</dt>
                      <dd>{item.error_code}</dd>
                    </div>
                  )}
                  <div>
                    <dt>{t("Account version")}</dt>
                    <dd>{item.account_version}</dd>
                  </div>
                  <div>
                    <dt>{t("Credential generation")}</dt>
                    <dd>{item.credential_generation}</dd>
                  </div>
                </dl>
                {item.status === "outcome_unknown" && (
                  <p className={styles.hint}>
                    {t(
                      "The platform may have received this reply. Check the conversation before deciding whether another reply is needed. Refreshing does not resend it.",
                    )}
                  </p>
                )}
              </li>
            ))}
          </ol>
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No native reply observed for this run")}
            description={t(
              "A run can finish without calling the platform reply tool. Earlier runs may have no recorded reply observations.",
            )}
          />
        )
      )}
      <p className={styles.hint}>
        {t(
          "Confirmation means the platform accepted the reply. It does not mean a person read it.",
        )}
      </p>
    </div>
  );
}
