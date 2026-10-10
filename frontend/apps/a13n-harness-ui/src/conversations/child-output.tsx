import type { ReactNode } from "react";
import { useQuery, useInfiniteQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { MessageText } from "./message-text";
import styles from "./conversation.module.css";
const outputKey = (target: Schema<"SavedOutputTarget">) =>
  JSON.stringify(target);

export function ChildSavedOutputs({
  threadId,
  rootThreadId,
  executionId,
  fallback,
}: {
  threadId: string;
  rootThreadId: string;
  executionId: string;
  fallback: ReactNode;
}) {
  const { client } = useTransport();
  const output = useQuery({
    queryKey: ["child-saved-output", threadId, executionId],
    retry: (count, error) =>
      !(error instanceof ApiError && error.status < 500) && count < 2,
    queryFn: ({ signal }) =>
      result(
        client.GET(
          "/api/threads/{thread_id}/children/{execution_id}/saved-output",
          {
            params: {
              path: { thread_id: threadId, execution_id: executionId },
              query: { limit: 1 },
            },
            signal,
          },
        ),
      ),
  });
  const latest = output.data?.outputs.find(
    (item) =>
      item.target.location.kind === "child_text" &&
      item.target.location.activity == null,
  );
  return (
    <>
      <ErrorNotice
        error={
          output.error instanceof ApiError &&
          output.error.code === "saved_output_source_unavailable"
            ? undefined
            : output.error
        }
        retry={() => void output.refetch()}
      />
      {latest ? (
        <CompleteChildOutput
          key={outputKey(latest.target)}
          first={latest}
          rootThreadId={rootThreadId}
        />
      ) : (
        fallback
      )}
    </>
  );
}

function CompleteChildOutput({
  first,
  rootThreadId,
}: {
  first: Schema<"SavedOutputView">;
  rootThreadId: string;
}) {
  const { client } = useTransport();
  const output = useInfiniteQuery({
    queryKey: ["child-final-text", rootThreadId, outputKey(first.target)],
    initialPageParam: 0,
    initialData: { pages: [first], pageParams: [0] },
    staleTime: Infinity,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.POST("/api/threads/{thread_id}/saved-output", {
          params: {
            path: { thread_id: rootThreadId },
            query: { offset: pageParam },
          },
          body: first.target,
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_offset ?? undefined,
  });
  const text = output.data.pages.map((page) => page.text).join("");
  return (
    <section className={styles.childAnswer} aria-label="Latest saved result">
      <header>Latest saved result</header>
      <MessageText text={text} />
      {output.hasNextPage && (
        <small>
          Showing part of the saved result. Load the remaining text to review
          the complete source.
        </small>
      )}
      <ErrorNotice error={output.error} />
      {output.hasNextPage && (
        <Button
          variant="outline"
          loading={output.isFetchingNextPage}
          onClick={() => void output.fetchNextPage()}
        >
          Load more result
        </Button>
      )}
    </section>
  );
}
