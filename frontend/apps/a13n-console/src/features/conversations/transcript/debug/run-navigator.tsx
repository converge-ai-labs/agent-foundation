import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import {
  CaretDownIcon,
  CaretLeftIcon,
  CaretRightIcon,
} from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { useRunOutline } from "./outline";
import { RunOutline, RunRow, RunPages } from "./run-outline";
import outline from "./run-outline.module.css";
import styles from "./details.module.css";

/** The current Thread's Runs in a wide outline or a compact navigation menu. */
export function RunNavigator({
  thread,
  runId,
}: {
  thread: Schema["ThreadView"];
  runId: string;
}) {
  const { t } = useTranslation();
  const tree = useRunOutline(thread, runId);
  const [pending, setPending] = useState<{ id: string; offset: number } | null>(
    null,
  );
  useEffect(() => {
    if (!pending) return;
    if (pending.id !== tree.inView || tree.error) {
      setPending(null);
      return;
    }
    const current = tree.nodes.findIndex((node) => node.run.id === pending.id);
    const next = tree.nodes[current + pending.offset]?.run;
    if (next) {
      setPending(null);
      tree.open(next);
    }
  }, [pending, tree]);
  // A lone Run has nowhere to go: its own heading already names it.
  if (tree.total < 2) return null;
  const position = tree.nodes.findIndex((node) => node.run.id === tree.inView);
  const step = (offset: number) => {
    const next = tree.nodes[position + offset]?.run;
    if (next) tree.open(next);
    else {
      setPending({ id: tree.inView, offset });
      if (offset < 0) tree.loadOlder();
      else tree.loadNewer();
    }
  };
  return (
    <div className={styles.navigator} data-run-index>
      <RunOutline outline={tree} />
      <div className={styles.navigatorPill}>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Previous run")}
          title={t("Previous run")}
          disabled={tree.loading || (position <= 0 && !tree.hasOlder)}
          onClick={() => step(-1)}
        >
          <CaretLeftIcon size={13} aria-hidden="true" />
        </Button>
        <Menu>
          <MenuTrigger
            render={
              <Button
                variant="ghost"
                size="sm"
                type="button"
                className={styles.navigatorLabel}
              />
            }
          >
            {tree.label}
            <CaretDownIcon size={11} aria-hidden="true" />
          </MenuTrigger>
          <MenuPopup align="center" className={styles.navigatorPopup}>
            <RunPages outline={tree} direction="older" menu />
            {tree.nodes.map((node) => (
              <MenuItem
                key={node.run.id}
                className={`${outline.row} ${outline.menuRow}`}
                data-current={node.run.id === tree.inView || undefined}
                onClick={() => tree.open(node.run)}
              >
                <RunRow node={node} />
              </MenuItem>
            ))}
            <RunPages outline={tree} direction="newer" menu />
          </MenuPopup>
        </Menu>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Next run")}
          title={t("Next run")}
          disabled={
            tree.loading ||
            position < 0 ||
            (position >= tree.nodes.length - 1 && !tree.hasNewer)
          }
          onClick={() => step(1)}
        >
          <CaretRightIcon size={13} aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
