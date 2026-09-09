import { useState } from "react";
import {
  BookOpen,
  FileText,
  MoreHorizontal,
  SlidersHorizontal,
  ArrowLeft,
  FolderOpen,
} from "lucide-react";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  SearchInput,
  Menu,
  Picker,
  Select,
  SettingsRow,
  SettingsSection,
  Tabs,
} from "../src";
import type { Translate } from "./showcase";
interface DemoDocument {
  id: string;
  kind: string;
  title: [string, string];
  description: [string, string];
}
const documents: DemoDocument[] = [
  {
    id: "start",
    kind: "guide",
    title: ["Getting started", "快速开始"],
    description: ["A quiet place to begin.", "从清晰的起点开始。"],
  },
  {
    id: "principles",
    kind: "guide",
    title: ["Design principles", "设计原则"],
    description: [
      "Make the next step easy to understand.",
      "让下一步操作易于理解。",
    ],
  },
  {
    id: "interaction",
    kind: "reference",
    title: ["Interaction patterns", "交互模式"],
    description: [
      "Consistent choices across every surface.",
      "在不同界面保持一致的操作。",
    ],
  },
];
export function CollectionDemo({ t }: { t: Translate }) {
  const [visibility, setVisibility] = useState("workspace");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [selected, setSelected] = useState(documents[0]);
  const [tab, setTab] = useState("list");
  const [message, setMessage] = useState("");
  const visible = documents.filter(
    (doc) =>
      (kind === "all" || doc.kind === kind) &&
      t(...doc.title)
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const reset = () => {
    setQuery("");
    setKind("all");
  };
  return (
    <>
      <div className="page-intro">
        <div className="eyebrow">
          {t("COMPOSITION / COLLECTION", "组合场景 / 集合")}
        </div>
        <h1>{t("Library", "资料库")}</h1>
        <p>
          {t(
            "A content-first list with just enough structure.",
            "以内容为中心，辅以恰到好处的结构。",
          )}
        </p>
      </div>
      <Tabs
        label={t("Library views", "资料库视图")}
        value={tab}
        onValueChange={setTab}
        items={[
          {
            value: "list",
            label: t("All documents", "所有文档"),
            content: (
              <div className="collection-panel">
                <div className="collection-toolbar">
                  <SearchInput
                    label={t("Search documents", "搜索文档")}
                    placeholder={t("Search documents…", "搜索文档…")}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  <div className="row">
                    <Picker
                      label={t("Document type", "文档类型")}
                      placeholder={t("Filter", "筛选")}
                      emptyMessage={t("No matching types.", "没有匹配类型。")}
                      value={kind}
                      onValueChange={setKind}
                      groups={[
                        {
                          label: t("Type", "类型"),
                          options: [
                            {
                              value: "all",
                              label: t("All types", "所有类型"),
                              icon: <SlidersHorizontal size={14} />,
                            },
                            {
                              value: "guide",
                              label: t("Guides", "指南"),
                              icon: <BookOpen size={14} />,
                            },
                            {
                              value: "reference",
                              label: t("Reference", "参考资料"),
                              icon: <FileText size={14} />,
                            },
                          ],
                        },
                      ]}
                    />
                    <Menu
                      label={t("Library actions", "资料库操作")}
                      trigger={
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<MoreHorizontal size={16} />}
                          aria-label={t("Library actions", "资料库操作")}
                        />
                      }
                      groups={[
                        {
                          actions: [
                            {
                              id: "reset",
                              label: t("Reset filters", "重置筛选"),
                              onSelect: reset,
                            },
                            {
                              id: "help",
                              label: t("Show guidance", "显示操作提示"),
                              description: t(
                                "Keyboard and navigation",
                                "键盘操作与导航",
                              ),
                              onSelect: () =>
                                setMessage(
                                  t(
                                    "Tab moves between controls. Arrow keys move through menus.",
                                    "Tab 切换控件，方向键浏览菜单。",
                                  ),
                                ),
                            },
                          ],
                        },
                        {
                          actions: [
                            {
                              id: "unavailable",
                              label: t("Export collection", "导出集合"),
                              description: t(
                                "Unavailable in this preview",
                                "当前预览不提供此操作",
                              ),
                              disabled: true,
                              onSelect: () => {},
                            },
                          ],
                        },
                      ]}
                    />
                  </div>
                </div>
                <div className="list-caption">
                  <span>{t("Documents", "文档")}</span>
                  <span>{visible.length}</span>
                </div>
                {visible.length ? (
                  visible.map((doc) => (
                    <button
                      className="document-row"
                      key={doc.id}
                      onClick={() => {
                        setSelected(doc);
                        setTab("details");
                      }}
                    >
                      <FileText size={16} />
                      <span className="document-copy">
                        <strong>{t(...doc.title)}</strong>
                        <span>{t(...doc.description)}</span>
                      </span>
                      <span className="document-kind">
                        {doc.kind === "guide"
                          ? t("Guide", "指南")
                          : t("Reference", "参考资料")}
                      </span>
                    </button>
                  ))
                ) : (
                  <EmptyState
                    icon={<FolderOpen size={22} />}
                    title={t("No documents found", "未找到文档")}
                    description={t(
                      "Try another name or clear the filters to see all documents.",
                      "换个名称试试，或清除筛选查看所有文档。",
                    )}
                    action={
                      <Button size="sm" onClick={reset}>
                        {t("Clear filters", "清除筛选")}
                      </Button>
                    }
                  />
                )}
                <div role="status" className="inline-status">
                  {message}
                </div>
              </div>
            ),
          },
          {
            value: "details",
            label: t("Details", "详情"),
            content: selected && (
              <div className="detail-layout">
                <article className="document-body">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<ArrowLeft size={14} />}
                    onClick={() => setTab("list")}
                  >
                    {t("Back to documents", "返回文档")}
                  </Button>
                  <h2>{t(...selected.title)}</h2>
                  <p>{t(...selected.description)}</p>
                  <h3>{t("Clarity through consistency", "一致带来清晰")}</h3>
                  <p>
                    {t(
                      "Use spacing to separate ideas. Keep related actions close to their content, and make every interactive element work with a keyboard.",
                      "用留白区分内容，让相关操作靠近内容，并确保每个交互元素都可用键盘操作。",
                    )}
                  </p>
                  <p>
                    {t(
                      "Keep the interface calm even as more information becomes available.",
                      "即使信息增多，界面仍应保持清晰有序。",
                    )}
                  </p>
                </article>
                <aside className="properties">
                  <SettingsSection title={t("Properties", "属性")}>
                    <SettingsRow label={t("Type", "类型")}>
                      <Badge>
                        {selected.kind === "guide"
                          ? t("Guide", "指南")
                          : t("Reference", "参考资料")}
                      </Badge>
                    </SettingsRow>
                    <SettingsRow
                      label={t("Visibility", "可见范围")}
                      description={t("Preview content only", "仅为预览内容")}
                    >
                      <Select
                        label={t("Visibility", "可见范围")}
                        placeholder={t("Choose visibility", "选择可见范围")}
                        size="sm"
                        variant="ghost"
                        value={visibility}
                        onValueChange={setVisibility}
                        options={[
                          {
                            value: "workspace",
                            label: t("Workspace", "工作空间"),
                          },
                          { value: "private", label: t("Only me", "仅自己") },
                        ]}
                      />
                    </SettingsRow>
                  </SettingsSection>
                  <Input
                    label={t("Local note", "本地笔记")}
                    placeholder={t("Write a short note…", "记一条简短笔记…")}
                    hint={t(
                      "Stays in this preview until you switch views.",
                      "切换视图前保留在当前预览中。",
                    )}
                  />
                </aside>
              </div>
            ),
          },
        ]}
      />
    </>
  );
}
