import { AguiContent, Button, readContentParts } from "a13n-ui";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import type { ContentRef, DisplayItem } from "a13n-ui/display";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { JsonView } from "../../shared/forms";
import { MarkdownContent } from "../../shared/markdown";
import { CopyButton } from "../../shared/identity";

const Context = createContext<{
  runId: string;
  items: ReadonlyMap<string, DisplayItem>;
} | null>(null);

export function useStoredReference(itemId: string, field: string) {
  return useContext(Context)?.items.get(itemId)?.content_refs?.[field];
}

/** References only. Loaded values belong to the open field, never the live normalizer or a query cache. */
export function StoredContents({
  runId,
  items = [],
  children,
}: {
  runId: string;
  items?: readonly DisplayItem[];
  children: ReactNode;
}) {
  return (
    <Context.Provider
      value={{ runId, items: new Map(items.map((item) => [item.id, item])) }}
    >
      {children}
    </Context.Provider>
  );
}

function Value({ field, value }: { field: string; value: unknown }) {
  if (field === "text" && typeof value === "string")
    return <MarkdownContent text={value} />;
  const parts = field === "result_parts" ? readContentParts(value) : undefined;
  return parts ? <AguiContent parts={parts} /> : <JsonView value={value} />;
}

/** The saved value replaces its preview in this field only; changing the reference starts a fresh read. */
export function StoredContent({
  itemId,
  field,
  value,
  renderValue,
}: {
  itemId: string;
  field: string;
  value?: unknown;
  renderValue?: (value: unknown) => ReactNode;
}) {
  const context = useContext(Context);
  const reference = context?.items.get(itemId)?.content_refs?.[field];
  if (!context || !reference)
    return renderValue ? (
      renderValue(value)
    ) : (
      <Value field={field} value={value} />
    );
  return (
    <LoadContent
      key={reference.id}
      runId={context.runId}
      field={field}
      reference={reference}
      renderValue={renderValue}
    />
  );
}

function LoadContent({
  runId,
  field,
  reference,
  renderValue,
}: {
  runId: string;
  field: string;
  reference: ContentRef;
  renderValue?: (value: unknown) => ReactNode;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [loaded, setLoaded] = useState<{ value: unknown }>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function load() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(undefined);
    try {
      const result = await client
        .workspace(workspace.id)
        .GET("/api/v1/runs/{run_id}/contents/{content_id}", {
          params: { path: { run_id: runId, content_id: reference.id } },
          signal: controller.signal,
        })
        .then(data);
      if (!controller.signal.aborted) setLoaded({ value: result.value });
    } catch (failure) {
      if (!controller.signal.aborted) {
        revalidateSession(failure);
        setError(failure);
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  return (
    <div aria-busy={loading || undefined}>
      {reference.truncated && (
        <p role="status">
          {t(
            "This content exceeded 16 MiB and was truncated. Only the saved prefix is available.",
          )}
        </p>
      )}
      {loaded ? (
        renderValue ? (
          renderValue(loaded.value)
        ) : (
          <Value field={field} value={loaded.value} />
        )
      ) : reference.media_type === "text/plain" ? (
        renderValue ? (
          renderValue(reference.preview)
        ) : (
          <Value field={field} value={reference.preview} />
        )
      ) : (
        <JsonView value={reference.preview} />
      )}
      {!loaded && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={loading}
          onClick={() => void load()}
        >
          {loading
            ? t("Loading content…")
            : reference.truncated
              ? t("Expand saved content")
              : t("Expand full content")}
        </Button>
      )}
      {loaded && field === "text" && typeof loaded.value === "string" && (
        <CopyButton
          value={loaded.value}
          iconOnly
          copyLabel={t("Copy message")}
        />
      )}
      <ErrorNotice error={error} retry={() => void load()} />
    </div>
  );
}

/** Tool and diagnostic entries may have more than one external field. */
export function ItemContents({
  itemId,
  exclude = [],
  className,
}: {
  itemId: string;
  exclude?: string[];
  className?: string;
}) {
  const context = useContext(Context),
    { t } = useTranslation();
  const item = context?.items.get(itemId);
  const refs = Object.entries(item?.content_refs ?? {}).filter(
    ([field]) => !exclude.includes(field),
  );
  if (!refs.length && item?.content.truncated !== true) return null;
  const labels: Record<string, string> = {
    text: "Message",
    arguments: "Arguments",
    result: "Result",
    result_parts: "Result",
    failure: "Error details",
    applied_edit: "Patch",
    input_media: "Attachments",
  };
  return (
    <div className={className}>
      {refs.map(([field, ref]) => (
        <details key={ref.id}>
          <summary>
            {ref.truncated ? t("Saved content") : t("Full content")}:{" "}
            {t(labels[field] ?? "Details")}
          </summary>
          <StoredContent itemId={itemId} field={field} />
        </details>
      ))}
      {item?.content.truncated === true && !refs.length && (
        <p role="status">
          {t(
            "This historical record was truncated. The remaining content is unavailable.",
          )}
        </p>
      )}
    </div>
  );
}

export function LegacyTruncation({ itemId }: { itemId: string }) {
  const context = useContext(Context),
    { t } = useTranslation();
  const item = context?.items.get(itemId);
  return item?.content.truncated === true &&
    !Object.keys(item.content_refs ?? {}).length ? (
    <p role="status">
      {t(
        "This historical record was truncated. The remaining content is unavailable.",
      )}
    </p>
  ) : null;
}

export function StoredObservations() {
  const context = useContext(Context);
  if (!context) return null;
  return (
    <>
      {[...context.items.values()]
        .filter(
          (item) =>
            item.kind === "observation" &&
            Object.keys(item.content_refs ?? {}).length,
        )
        .map((item) => (
          <section key={item.id}>
            <code>{String(item.content.name ?? "Observation")}</code>
            <ItemContents itemId={item.id} />
          </section>
        ))}
    </>
  );
}
