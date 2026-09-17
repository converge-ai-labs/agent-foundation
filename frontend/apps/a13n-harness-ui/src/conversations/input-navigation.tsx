import { useEffect, useState, type RefObject } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import { ListBullets } from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { inputAttachment } from "./input-content";
import type { LocalInput } from "./local-input";
import styles from "./input-navigation.module.css";

export function InputNavigation({
  threadId,
  continuation,
  localInputs,
  reader,
  revision,
  onSelect,
}: {
  threadId: string;
  continuation?: string | null;
  localInputs: LocalInput[];
  reader: RefObject<HTMLDivElement | null>;
  revision: unknown;
  onSelect: (id: string) => void;
}) {
  const { client } = useTransport();
  const [active, setActive] = useState<string>();
  const [open, setOpen] = useState(false);
  const query = useInfiniteQuery({
    queryKey: ["thread", threadId, "inputs", continuation],
    enabled: !!continuation,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/inputs", {
          params: {
            path: { thread_id: threadId },
            query: {
              expected_continuation_id: continuation ?? undefined,
              cursor: pageParam,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  // Fetch only the small directory, never the intervening execution transcript.
  useEffect(() => {
    if (query.hasNextPage && !query.isFetching && !query.isError)
      void query.fetchNextPage();
  }, [query.hasNextPage, query.isFetching, query.isError, query.fetchNextPage]);
  const items: Pick<
    Schema<"TranscriptTurn">,
    "turn_id" | "preview" | "output_preview"
  >[] = query.data?.pages.flatMap((page) => page.turns) ?? [];
  for (const input of localInputs) {
    if (
      input.action !== "send" ||
      input.state !== "accepted" ||
      items.some((item) => item.turn_id === input.id)
    )
      continue;
    items.push({
      turn_id: input.id,
      preview:
        input.parts
          .map(
            (part) => inputAttachment(part.metadata)?.name ?? part.text ?? "",
          )
          .join(" ")
          .trim()
          .slice(0, 512) || "Attachment",
    });
  }
  useEffect(() => {
    const element = reader.current;
    if (!element) return;
    const update = () => {
      const top = element.getBoundingClientRect().top + 80;
      const sections = [
        ...element.querySelectorAll<HTMLElement>("[data-turn-id]"),
      ];
      const current =
        sections.find(
          (section) => section.getBoundingClientRect().bottom > top,
        ) ?? sections.at(-1);
      setActive(current?.dataset.turnId);
    };
    update();
    element.addEventListener("scroll", update, { passive: true });
    const resize =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(update);
    if (element.firstElementChild) resize?.observe(element.firstElementChild);
    return () => {
      element.removeEventListener("scroll", update);
      resize?.disconnect();
    };
  }, [reader, revision]);
  const select = (id: string) => {
    setOpen(false);
    setActive(id);
    onSelect(id);
  };
  if (!items.length && !query.isError) return null;
  const retry = query.isError && (
    <Button variant="ghost" size="sm" onClick={() => void query.refetch()}>
      Retry input navigation
    </Button>
  );
  return (
    <>
      <nav className={styles.rail} aria-label="Input timeline">
        {items.map((item, index) => (
          <Tooltip key={item.turn_id}>
            <TooltipTrigger
              render={<button type="button" className={styles.tick} />}
              aria-label={`Input ${index + 1}: ${item.preview}`}
              aria-current={active === item.turn_id ? "location" : undefined}
              onClick={() => select(item.turn_id)}
            >
              <span />
            </TooltipTrigger>
            <TooltipPopup
              side="right"
              align="center"
              className={styles.preview}
            >
              <small>Input {index + 1}</small>
              <p>{item.preview}</p>
              {item.output_preview && (
                <div className={styles.output}>
                  <small>Output</small>
                  <p>{item.output_preview}</p>
                </div>
              )}
            </TooltipPopup>
          </Tooltip>
        ))}
        {retry}
      </nav>
      <div className={styles.mobile}>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger
            render={<Button variant="ghost" size="sm" />}
            aria-label="Input history"
          >
            <ListBullets /> Inputs
          </PopoverTrigger>
          <PopoverPopup side="bottom" align="start" className={styles.menu}>
            <PopoverTitle>Input history</PopoverTitle>
            <nav aria-label="Input history">
              {items.map((item, index) => (
                <button
                  type="button"
                  key={item.turn_id}
                  aria-current={
                    active === item.turn_id ? "location" : undefined
                  }
                  onClick={() => select(item.turn_id)}
                >
                  <small>{index + 1}</small>
                  <span className={styles.menuPreview}>
                    <span>{item.preview}</span>
                    {item.output_preview && (
                      <span className={styles.menuOutput}>
                        Output: {item.output_preview}
                      </span>
                    )}
                  </span>
                </button>
              ))}
            </nav>
            {retry}
          </PopoverPopup>
        </Popover>
      </div>
    </>
  );
}
