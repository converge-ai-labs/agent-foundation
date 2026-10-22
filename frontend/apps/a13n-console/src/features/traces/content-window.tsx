import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

// Keep nested measurements when an offscreen message is unmounted and revisited.
type Measurements = Map<unknown, Map<number, number>>;
const MeasurementContext = createContext<Measurements | null>(null);

export function ContentWindow({
  source,
  children,
}: {
  source: unknown;
  children: ReactNode;
}) {
  const measurements = useMemo<Measurements>(() => new Map(), [source]);
  return (
    <MeasurementContext value={measurements}>{children}</MeasurementContext>
  );
}

export function useContentMeasurements(source: unknown) {
  const context = useContext(MeasurementContext);
  const local = useMemo(() => new Map<number, number>(), [source]);
  if (!context) return local;
  if (!context.has(source)) context.set(source, new Map());
  return context.get(source)!;
}

export function contentScrollRoot(element: HTMLElement): HTMLElement | null {
  for (
    let parent = element.parentElement;
    parent;
    parent = parent.parentElement
  ) {
    if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY)) return parent;
  }
  return null;
}

/** Mount expensive renderers near the reader, retaining measured space elsewhere. */
export function ContentChunk({
  index,
  heights,
  estimate,
  children,
}: {
  index: number;
  heights: Map<number, number>;
  estimate: number;
  children: () => ReactNode;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(index === 0);
  useEffect(() => {
    const node = element.current!;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        const next =
          entry.isIntersecting || node.contains(document.activeElement);
        if (!next && node.childElementCount)
          heights.set(index, node.getBoundingClientRect().height);
        setVisible(next);
      },
      { root: contentScrollRoot(node), rootMargin: "800px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [heights, index]);
  useEffect(() => {
    if (!visible) return;
    const node = element.current!;
    const measure = () =>
      heights.set(index, node.getBoundingClientRect().height);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible, heights, index]);
  return (
    <div
      ref={element}
      data-content-chunk={index}
      aria-hidden={!visible || undefined}
      style={visible ? undefined : { height: heights.get(index) ?? estimate }}
    >
      {visible ? children() : null}
    </div>
  );
}
