import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

/**
 * The transcript follows new output until the reader scrolls away, and stops
 * moving while an earlier page is being prepended. The scrolling viewport is
 * the stage that owns the run, marked with `data-session-stage`, so an embedded
 * transcript keeps its own scroll container.
 */
export function useTranscriptScroll(
  content: RefObject<HTMLElement | null>,
  ready: boolean,
) {
  const [following, setFollowing] = useState(true);
  const viewport = useRef<HTMLElement | null>(null);
  const resume = useRef<(() => void) | null>(null);
  useEffect(() => {
    const element = content.current;
    const stage = element?.closest("[data-session-stage]");
    if (!element || !(stage instanceof HTMLElement)) return;
    viewport.current = stage;
    let follow = true;
    const onScroll = () => {
      follow = stage.scrollHeight - stage.scrollTop - stage.clientHeight < 100;
      setFollowing(follow);
    };
    // Sending a message always returns the reader to the live end.
    resume.current = () => {
      follow = true;
      setFollowing(true);
    };
    const observer = new ResizeObserver(() => {
      if (follow && !stage.dataset.loadingEarlier)
        stage.scrollTop = stage.scrollHeight;
    });
    observer.observe(element);
    stage.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      stage.removeEventListener("scroll", onScroll);
      resume.current = null;
    };
  }, [content, ready]);
  const jumpToLatest = useCallback(() => {
    resume.current?.();
    const stage = viewport.current;
    stage?.scrollTo({ top: stage.scrollHeight, behavior: "smooth" });
  }, []);
  return { following, jumpToLatest };
}
