import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import {
  CaretDownIcon,
  CaretLeftIcon,
  CaretRightIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { useRunOutline } from "./outline";
import { RunOutline, RunRow } from "./run-outline";
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
  if (tree.nodes.length === 0) return null;
  const position = tree.nodes.findIndex((node) => node.run.id === tree.inView);
  const step = (offset: number) => {
    const next = tree.nodes[position + offset]?.run;
    if (next) tree.open(next);
  };
  return (
    <div className={styles.navigator}>
      <RunOutline outline={tree} />
      <div className={styles.navigatorPill}>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Previous run")}
          title={t("Previous run")}
          disabled={position <= 0}
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
          </MenuPopup>
        </Menu>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Next run")}
          title={t("Next run")}
          disabled={position < 0 || position >= tree.nodes.length - 1}
          onClick={() => step(1)}
        >
          <CaretRightIcon size={13} aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
