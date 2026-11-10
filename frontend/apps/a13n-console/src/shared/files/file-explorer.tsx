import {
  CaretRightIcon,
  FileTextIcon,
  FolderIcon,
  DotsThreeOutlineVerticalIcon,
} from "@phosphor-icons/react";
import {
  Button,
  ContextMenu,
  ContextMenuTrigger,
  Menu,
  MenuPopup,
  MenuTrigger,
} from "a13n-ui";
import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { FileNode } from "./tree";
import styles from "./files.module.css";

/** One roving tab stop; folder and file navigation follow the ARIA tree pattern. */
export function FileExplorer({
  nodes,
  selected,
  select,
  marker,
  fileMenu,
  label,
}: {
  nodes: FileNode[];
  selected: string;
  select: (path: string) => void;
  label: string;
  marker?: (path: string) => ReactNode;
  fileMenu?: (path: string) => ReactNode;
}) {
  const { t } = useTranslation();
  const [closed, setClosed] = useState<Set<string>>(() => new Set());
  const [focused, setFocused] = useState(selected);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const visible: { node: FileNode; level: number; parent?: string }[] = [];
  function flatten(items: FileNode[], level = 1, parent?: string) {
    for (const node of items) {
      visible.push({ node, level, parent });
      if (node.children && !closed.has(node.path))
        flatten(node.children, level + 1, node.path);
    }
  }
  flatten(nodes);
  const focusPath = visible.some(({ node }) => node.path === focused)
    ? focused
    : visible[0]?.node.path;
  function focus(path: string | undefined) {
    if (path) {
      setFocused(path);
      buttons.current.get(path)?.focus();
    }
  }
  function toggle(path: string, close: boolean) {
    setClosed((current) => {
      const next = new Set(current);
      if (close) next.add(path);
      else next.delete(path);
      return next;
    });
  }
  return (
    <div role="tree" aria-label={label} className={styles.explorer}>
      {visible.map(({ node, level, parent }, index) => {
        const folder = !!node.children,
          open = folder && !closed.has(node.path);
        const control = (
          <button
            type="button"
            role="treeitem"
            aria-level={level}
            aria-expanded={folder ? open : undefined}
            aria-selected={!folder ? selected === node.path : undefined}
            tabIndex={focusPath === node.path ? 0 : -1}
            title={node.path}
            ref={(element) => {
              if (element) buttons.current.set(node.path, element);
              else buttons.current.delete(node.path);
            }}
            className={styles.fileButton}
            onFocus={() => setFocused(node.path)}
            onClick={() =>
              folder ? toggle(node.path, open) : select(node.path)
            }
            onKeyDown={(event) => {
              let handled = true;
              switch (event.key) {
                case "ArrowDown":
                  focus(visible[index + 1]?.node.path);
                  break;
                case "ArrowUp":
                  focus(visible[index - 1]?.node.path);
                  break;
                case "Home":
                  focus(visible[0]?.node.path);
                  break;
                case "End":
                  focus(visible.at(-1)?.node.path);
                  break;
                case "ArrowRight":
                  if (folder) {
                    if (!open) toggle(node.path, false);
                    else focus(visible[index + 1]?.node.path);
                  }
                  break;
                case "ArrowLeft":
                  if (open) toggle(node.path, true);
                  else focus(parent);
                  break;
                default:
                  handled = false;
              }
              if (handled) event.preventDefault();
            }}
          >
            {folder ? (
              <>
                <CaretRightIcon
                  size={12}
                  className={open ? styles.expanded : undefined}
                />
                <FolderIcon size={14} />
              </>
            ) : (
              <FileTextIcon size={14} />
            )}
            <span>{node.name}</span>
            {!folder && marker?.(node.path)}
          </button>
        );
        return (
          <div
            key={node.path}
            role="none"
            className={styles.fileRow}
            style={{ paddingLeft: (level - 1) * 12 }}
            data-selected={(!folder && selected === node.path) || undefined}
          >
            {!folder && fileMenu ? (
              <ContextMenu>
                <ContextMenuTrigger render={control} />
                <MenuPopup align="start">{fileMenu(node.path)}</MenuPopup>
              </ContextMenu>
            ) : (
              control
            )}
            {!folder && fileMenu && (
              <Menu>
                <MenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className={styles.fileActions}
                      aria-label={t("Actions for {{path}}", {
                        path: node.path,
                      })}
                    />
                  }
                >
                  <DotsThreeOutlineVerticalIcon weight="fill" />
                </MenuTrigger>
                <MenuPopup align="start">{fileMenu(node.path)}</MenuPopup>
              </Menu>
            )}
          </div>
        );
      })}
    </div>
  );
}
