import { ArrowLeftIcon, ArrowRightIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";

import { useTranslation } from "react-i18next";
import type { useCursor } from "./use-cursor";
export function Pagination({
  page,
  next,
}: {
  page: ReturnType<typeof useCursor>;
  next?: string | null;
}) {
  const { t } = useTranslation();
  if (!page.previous && !next) return null;
  return (
    <div className="flex justify-end gap-2 py-4">
      <Button
        size="sm"
        variant="outline"
        disabled={!page.previous}
        onClick={page.previous}
        type="button"
      >
        <ArrowLeftIcon aria-hidden="true" />
        {t("Previous")}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={!next}
        onClick={() => next && page.next(next)}
        type="button"
      >
        {t("Next")}
        <ArrowRightIcon aria-hidden="true" />
      </Button>
    </div>
  );
}
