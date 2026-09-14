import { lazy, Suspense, type ComponentProps } from "react";

const Editor = lazy(() =>
  import("./editor-view").then((module) => ({ default: module.SourceEditor })),
);
export function SourceEditor(props: ComponentProps<typeof Editor>) {
  return (
    <Suspense fallback={<p role="status">Loading source editor…</p>}>
      <Editor {...props} />
    </Suspense>
  );
}
