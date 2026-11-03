import { useTranslation } from "../i18n";
import { lazy, Suspense, type ComponentProps } from "react";

const Editor = lazy(() =>
  import("./editor-view").then((module) => ({ default: module.SourceEditor })),
);
export function SourceEditor(props: ComponentProps<typeof Editor>) {
  const { t } = useTranslation();
  return (
    <Suspense fallback={<p role="status">{t("Loading source editor…")}</p>}>
      <Editor {...props} />
    </Suspense>
  );
}
