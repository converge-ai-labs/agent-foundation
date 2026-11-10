import { useContext, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "./i18n";
import { useSelectors, useTransport } from "./transport/context";
import {
  ApiError,
  EmptyResponseError,
  NetworkError,
  result,
} from "./transport/client";
import { ErrorNotice, PageHeader } from "./shell/ui";
import { BotFields } from "./configuration/bot-fields";
import { DraftContext } from "./configuration/drafts";
import { useRootConfiguration } from "./configuration/root-configuration";
import { readDocument, updateDocument } from "./configuration/documents";
import styles from "./bot.module.css";

export function BotSetup() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const root = useRootConfiguration();
  const selectors = useSelectors();
  const drafts = useContext(DraftContext);
  const queries = useQueryClient();
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [uncertain, setUncertain] = useState(false);
  const applyEdits = (source: string) =>
    Object.entries(edits).reduce(
      (content, [key, value]) =>
        updateDocument(content, ["webui", "bot", key], value),
      source,
    );
  const source = applyEdits(root.data?.content ?? "");
  const document = readDocument(source)?.toJS();
  const agent = selectors.data?.agents.find(
    (agent) => agent.agent_id === document?.defaults?.agent,
  );
  const modelId = document?.webui?.bot?.model ?? agent?.model_id;
  const canEnable = selectors.data?.models?.some(
    (model) => model.model_id === modelId,
  );
  const enable = useMutation({
    retry: false,
    mutationFn: async () => {
      if (!root.path) throw new Error(t("Couldn't load global settings."));
      const path = root.path;
      const draft = drafts.get(path);
      if (draft && draft.content !== draft.base)
        throw new Error(
          t("Save or discard your global settings changes first."),
        );
      const latest = await result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      );
      if (!latest.content || !latest.writable)
        throw new Error(t("Global settings are read-only."));
      const current = drafts.get(path);
      if (current && current.content !== current.base)
        throw new Error(
          t("Save or discard your global settings changes first."),
        );
      // Apply only edited fields to the latest source, retaining concurrent settings and YAML nodes.
      const content = updateDocument(
        applyEdits(latest.content),
        ["webui", "bot", "enabled"],
        true,
      );
      const publication = await result(
        client.PUT("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
          body: { content },
        }),
      );
      drafts.set(path, {
        content,
        base: content,
        digest: publication.source_digest,
        replacement: false,
      });
    },
    onSuccess: () => {
      setUncertain(false);
      void queries.invalidateQueries();
    },
    onError: (error) =>
      setUncertain(
        !(error instanceof ApiError && error.status < 500) &&
          (error instanceof NetworkError ||
            error instanceof EmptyResponseError ||
            error instanceof ApiError),
      ),
  });
  const check = useMutation({
    retry: false,
    mutationFn: () => result(client.GET("/api/bot")),
    onSuccess: (snapshot) => {
      queries.setQueryData(["bot"], snapshot);
      setUncertain(false);
    },
  });
  return (
    <main className={styles.setup}>
      <PageHeader
        title={t("Bot")}
        description={t(
          "Keeps track of ongoing work across your chats and checks in on a schedule.",
        )}
      />
      {root.data?.content && (
        <BotFields
          source={source}
          onChange={(_, key, value) =>
            setEdits((current) => ({ ...current, [key]: value }))
          }
          setup
        />
      )}
      <ErrorNotice error={root.error} retry={() => void root.refetch()} />
      <ErrorNotice
        error={check.error || enable.error}
        retry={!uncertain ? () => enable.mutate() : undefined}
      />
      <div className={styles.setupActions}>
        <p>
          {t(
            "Turning on saves these settings without starting a run. The first Heartbeat is scheduled automatically; you can also message Bot anytime.",
          )}
        </p>
        {uncertain ? (
          <Button disabled={check.isPending} onClick={() => check.mutate()}>
            {t("Check again")}
          </Button>
        ) : (
          <Button
            disabled={!root.data?.writable || !canEnable || enable.isPending}
            onClick={() => enable.mutate()}
          >
            {enable.isPending ? t("Saving…") : t("Turn on Bot")}
          </Button>
        )}
      </div>
    </main>
  );
}
