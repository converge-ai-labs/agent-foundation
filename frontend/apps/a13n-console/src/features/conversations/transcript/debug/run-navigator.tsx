import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import {
  CaretDownIcon,
  CaretLeftIcon,
  CaretRightIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { useRunOutline } from "./outline";
import {
  OutlineBranch,
  RunOutline,
  RunRow,
  hasNestedGroups,
} from "./run-outline";
import outline from "./run-outline.module.css";
import styles from "./details.module.css";

/**
 * Where the reader is, and every other Run they can go to. One tree, two
 * presentations: an outline in the left gutter where the page is wide enough
 * for one, and a pinned pill that opens the same tree where it is not.
 */
export function RunNavigator({
  thread,
  runId,
}: {
  thread: Schema["ThreadResource"];
  runId: string;
}) {
  const { t } = useTranslation();
  const tree = useRunOutline(thread, runId);
  if (tree.sequence.length <= 1) return null;
  const position = tree.sequence.findIndex((run) => run.id === tree.inView);
  const step = (offset: number) => {
    const next = tree.sequence[position + offset];
    if (next) tree.open(next);
  };
  const named = hasNestedGroups(tree.groups);
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
            {tree.groups.map((group) => (
              <OutlineBranch
                key={group.thread.id}
                group={group}
                outline={tree}
                labelled={named}
                row={(node, current) => (
                  <MenuItem
                    className={`${outline.row} ${outline.menuRow}`}
                    data-current={current || undefined}
                    onClick={() => tree.open(node.run)}
                  >
                    <RunRow node={node} />
                  </MenuItem>
                )}
              />
            ))}
          </MenuPopup>
        </Menu>
        <Button
          size="icon-sm"
          variant="ghost"
          type="button"
          aria-label={t("Next run")}
          title={t("Next run")}
          disabled={position < 0 || position >= tree.sequence.length - 1}
          onClick={() => step(1)}
        >
          <CaretRightIcon size={13} aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
