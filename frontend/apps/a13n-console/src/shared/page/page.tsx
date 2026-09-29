import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import {
  PageActionClaimed,
  PageActionsTarget,
  PageEmptyAction,
} from "./page-actions";
import styles from "./page.module.css";

/**
 * List page anatomy: back link, title with an optional count, one-line
 * description, one primary action, then the toolbar and the collection.
 */
export function Page({
  title,
  count,
  titleAction,
  description,
  actions,
  toolbar,
  back,
  backLabel,
  className,
  children,
}: {
  title: string;
  count?: number;
  titleAction?: ReactNode;
  description?: string;
  actions?: ReactNode;
  toolbar?: ReactNode;
  back?: string;
  backLabel?: string;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(
    null,
  );
  // One offer of the primary action per screen: when the collection is empty
  // and its empty state offers it, the header stays quiet.
  const [offeredBelow, setOfferedBelow] = useState(false);
  return (
    <PageEmptyAction value={setOfferedBelow}>
      <PageActionClaimed value={offeredBelow}>
        <PageActionsTarget value={actionsTarget}>
          <div className={`${styles.page} ${className ?? ""}`}>
            {back && (
              <Link className={styles.back} to={back}>
                <ArrowLeftIcon size={13} />
                {backLabel ?? t("Back")}
              </Link>
            )}
            <header className={styles.header}>
              <div className="min-w-0">
                <div className={styles.titleRow}>
                  <h1>{title}</h1>
                  {count !== undefined && (
                    <span className={styles.count}>{count}</span>
                  )}
                  {titleAction}
                </div>
                {description && (
                  <p className={styles.description}>{description}</p>
                )}
              </div>
              <div className={styles.actions} ref={setActionsTarget}>
                {actions && (
                  <div className={styles.actions} hidden={offeredBelow}>
                    {actions}
                  </div>
                )}
              </div>
            </header>
            {toolbar && <div className={styles.toolbar}>{toolbar}</div>}
            <div className={styles.body}>{children}</div>
          </div>
        </PageActionsTarget>
      </PageActionClaimed>
    </PageEmptyAction>
  );
}
