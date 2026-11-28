import { createContext, useContext, type ReactNode } from "react";
import { CaretRightIcon } from "@phosphor-icons/react";
import {
  Tooltip,
  TooltipPopup,
  TooltipProvider,
  TooltipTrigger,
} from "./components/tooltip";
import { safeCitationUrl, type NumberedSource } from "./citations";
import styles from "./citation-markdown.module.css";

type Labels = {
  unavailable: string;
  document: string;
  source: (number: number, name: string) => string;
  sources: (count: number) => string;
};

const CitationContext = createContext<{
  sources: readonly NumberedSource[];
  labels: Labels;
  allowLink?: (url: URL) => boolean;
} | null>(null);

function displayHostname(url: URL): string {
  return url.hostname.replace(/^www\./i, "");
}

export function CitationMarkdown({
  sources,
  labels,
  allowLink,
  children,
}: {
  sources: readonly NumberedSource[];
  labels: Labels;
  allowLink?: (url: URL) => boolean;
  children: ReactNode;
}) {
  return (
    <CitationContext.Provider value={{ sources, labels, allowLink }}>
      <TooltipProvider>
        {children}
        {sources.length > 0 && (
          <details className={styles.sources} data-reply-decoration="">
            <summary>
              <CaretRightIcon aria-hidden="true" />
              <span>{labels.sources(sources.length)}</span>
            </summary>
            <ol>
              {sources.map((entry) => (
                <li key={entry.number}>
                  <SourceDetails entry={entry} />
                </li>
              ))}
            </ol>
          </details>
        )}
      </TooltipProvider>
    </CitationContext.Provider>
  );
}

function SourceDetails({
  entry,
  preview = false,
}: {
  entry: NumberedSource;
  preview?: boolean;
}) {
  const { labels, allowLink } = useContext(CitationContext)!;
  const { source } = entry;
  const locator = source.kind === "web" ? source.url : source.document_id;
  const url = source.kind === "web" ? safeCitationUrl(source.url) : undefined;
  const host = url ? displayHostname(url) : undefined;
  const title =
    source.title ||
    (source.kind === "document" ? labels.document : host || locator);
  return (
    <span className={styles.details} data-preview={preview ? "" : undefined}>
      <span className={styles.title}>
        {!preview && url && allowLink?.(url) !== false ? (
          <a href={url.href} target="_blank" rel="noopener noreferrer">
            {title}
          </a>
        ) : (
          title
        )}
      </span>
      {locator && locator !== title && (
        <span className={styles.locator}>{locator}</span>
      )}
      {source.excerpts?.map((excerpt, index) => (
        <span className={styles.excerpt} key={index}>
          {excerpt}
        </span>
      ))}
    </span>
  );
}

function SourceMark({ entry }: { entry: NumberedSource }) {
  const { labels, allowLink } = useContext(CitationContext)!;
  const { source, number } = entry;
  const url = source.kind === "web" ? safeCitationUrl(source.url) : undefined;
  const host = url ? displayHostname(url) : undefined;
  const name =
    source.title ||
    (source.kind === "web"
      ? (host ?? source.url)
      : (source.document_id ?? labels.document));
  const labelled = labels.source(number, name);
  return (
    <Tooltip>
      <TooltipTrigger
        className={styles.mark}
        data-reply-decoration=""
        aria-label={labelled}
        render={
          url && allowLink?.(url) !== false ? (
            <a href={url.href} target="_blank" rel="noopener noreferrer" />
          ) : (
            <span role="note" tabIndex={0} />
          )
        }
      >
        {host ? (
          <span className={styles.hostname} aria-hidden="true">
            {host}
          </span>
        ) : null}
        <span className={host ? styles.compact : undefined} aria-hidden="true">
          [{number}]
        </span>
      </TooltipTrigger>
      <TooltipPopup>
        <SourceDetails entry={entry} preview />
      </TooltipPopup>
    </Tooltip>
  );
}

export function CitationMark({
  node,
}: {
  node?: { properties: Record<string, unknown> };
}) {
  const context = useContext(CitationContext);
  if (!context) return null;
  const { sources, labels } = context;
  const data = node?.properties.dataCitationNumbers;
  const entries =
    typeof data === "string"
      ? data
          .split(",")
          .map((value) => sources[Number(value) - 1])
          .filter((entry) => entry !== undefined)
      : [];
  if (entries.length)
    return (
      <>
        {entries.map((entry) => (
          <SourceMark key={entry.number} entry={entry} />
        ))}
      </>
    );
  return (
    <Tooltip>
      <TooltipTrigger
        className={styles.mark}
        data-reply-decoration=""
        data-unavailable=""
        aria-label={labels.unavailable}
        render={<span role="note" tabIndex={0} />}
      >
        <span aria-hidden="true">[?]</span>
      </TooltipTrigger>
      <TooltipPopup>{labels.unavailable}</TooltipPopup>
    </Tooltip>
  );
}
