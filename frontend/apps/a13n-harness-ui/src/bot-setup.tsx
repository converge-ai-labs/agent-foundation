import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Skeleton } from "a13n-ui";
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

function useBotSetup() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [active, setActive] = useState(false);
  const root = useRootConfiguration(active);
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
    (agent) =>
      agent.agent_id ===
      (document?.webui?.bot?.agent ?? document?.defaults?.agent),
  );
  const modelId = document?.webui?.bot?.model ?? agent?.model_id;
  const canEnable =
    (!document?.webui?.bot?.agent || !!agent) &&
    selectors.data?.models?.some((model) => model.model_id === modelId);
  const heartbeat = document?.webui?.bot?.heartbeat_enabled !== false;
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
      // Patch the latest source. The shared configuration API has no write precondition.
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
      // Keep a newer Settings draft and its original conflict metadata together:
      // its content may not include this publication's Bot settings.
      const after = drafts.get(path);
      if (!after || after.content === after.base)
        drafts.set(path, {
          content,
          base: content,
          digest: publication.source_digest,
          replacement: false,
        });
    },
    onSuccess: () => {
      setEdits({});
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
      if (snapshot.enabled) setEdits({});
      setUncertain(false);
    },
  });
  useEffect(() => {
    if (!Object.keys(edits).length) return;
    const protect = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [edits]);
  return {
    root,
    source,
    setEdits,
    canEnable,
    heartbeat,
    enable,
    check,
    uncertain,
    setActive,
  };
}

const BotSetupContext = createContext<ReturnType<typeof useBotSetup> | null>(
  null,
);
// Setup drafts and in-flight saves survive page navigation, just like Bot chat input.
export function BotSetupProvider({ children }: { children: ReactNode }) {
  const setup = useBotSetup();
  return <BotSetupContext value={setup}>{children}</BotSetupContext>;
}

export function BotSetup() {
  const { t } = useTranslation();
  const setup = useContext(BotSetupContext);
  if (!setup) throw new Error("Bot setup is missing.");
  const {
    root,
    source,
    setEdits,
    canEnable,
    heartbeat,
    enable,
    check,
    uncertain,
    setActive,
  } = setup;
  useEffect(() => setActive(true), [setActive]);
  return (
    <main className={styles.setup}>
      <PageHeader
        title={t("Bot")}
        description={t(
          "Keeps track of ongoing work across your chats and checks in on a schedule.",
        )}
      />
      {root.isPending && (
        <div
          className={styles.setupLoading}
          role="status"
          aria-label={t("Loading…")}
        >
          <Skeleton className="h-8 w-48 max-w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}
      {root.data?.content && (
        <fieldset
          className={styles.setupFields}
          disabled={enable.isPending || uncertain}
          inert={enable.isPending || uncertain}
        >
          <BotFields
            source={source}
            onChange={(_, key, value) => {
              if (!enable.isPending && !uncertain)
                setEdits((current) => ({ ...current, [key]: value }));
            }}
            setup
          />
        </fieldset>
      )}
      <ErrorNotice error={root.error} retry={() => void root.refetch()} />
      <ErrorNotice
        error={check.error || enable.error}
        retry={!uncertain ? () => enable.mutate() : undefined}
      />
      {root.data && !root.data.writable && (
        <p role="status">{t("Global settings are read-only.")}</p>
      )}
      <div className={styles.setupActions}>
        <p>
          {heartbeat
            ? t(
                "Turning on sends one visible first message to introduce your workspace using the selected model. Heartbeat checks follow the schedule; you can chat anytime.",
              )
            : t(
                "Turning on sends one visible first message to introduce your workspace using the selected model. Heartbeat is off; you can chat anytime.",
              )}
        </p>
        {uncertain ? (
          <Button loading={check.isPending} onClick={() => check.mutate()}>
            {t("Check again")}
          </Button>
        ) : (
          <Button
            disabled={!root.data?.writable || !canEnable}
            loading={enable.isPending}
            onClick={() => enable.mutate()}
          >
            {t("Turn on Bot")}
          </Button>
        )}
      </div>
    </main>
  );
}
