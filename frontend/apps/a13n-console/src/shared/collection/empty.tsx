import {
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  Empty as EmptyRoot,
  EmptyTitle,
} from "a13n-ui";
import { TrayIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useOffersPageAction } from "../page/page-actions";

/** Centred on a surface: icon tile, title, one line, and the primary action. */
export function Empty({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  useOffersPageAction(!!action);
  return (
    <EmptyRoot>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          {icon ?? <TrayIcon aria-hidden="true" />}
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </EmptyRoot>
  );
}
