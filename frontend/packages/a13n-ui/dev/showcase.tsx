import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Boxes,
  Component,
  Palette,
  Settings,
  Search,
  Moon,
  Sun,
  PanelLeftClose,
  PanelLeftOpen,
  Monitor,
  Smartphone,
} from "lucide-react";
import { Button, CommandPalette, Kbd, Select, Logo, Wordmark } from "../src";
import "../src/styles/index.css";
import "./showcase.css";
import { Foundations } from "./foundations";
import { Controls } from "./controls";
import { SettingsDemo } from "./settings-demo";
import { CollectionDemo } from "./collection-demo";
export type Translate = (en: string, zh: string) => string;
const pages = [
  { id: "foundations", en: "Foundations", zh: "基础规范", icon: Palette },
  { id: "components", en: "Components", zh: "基础组件", icon: Component },
  { id: "settings", en: "Settings", zh: "设置场景", icon: Settings },
  { id: "collection", en: "Collection", zh: "集合场景", icon: Boxes },
];
function currentPage() {
  const hash = location.hash.slice(1);
  return pages.some((page) => page.id === hash) ? hash : "settings";
}
function Showcase() {
  const [page, setPage] = useState(currentPage);
  const [language, setLanguage] = useState("en");
  const [dark, setDark] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [commands, setCommands] = useState(false);
  const t: Translate = (en, zh) => (language === "en" ? en : zh);
  useEffect(() => {
    document.documentElement.dataset.a13nTheme = dark ? "dark" : "light";
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  useEffect(() => {
    const change = () => setPage(currentPage());
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        event.key.toLowerCase() === "k" &&
        (event.metaKey || event.ctrlKey) &&
        !event.isComposing
      ) {
        event.preventDefault();
        setCommands((open) => !open);
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
  const title = pages.find((item) => item.id === page)!;
  return (
    <div className="a13n-root showroom" data-sidebar={sidebar}>
      <a
        className="skip-link"
        href="#showcase-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("showcase-content")?.focus();
        }}
      >
        {t("Skip to content", "跳至内容")}
      </a>
      <aside className="showroom-sidebar">
        <a className="brand" href="#settings">
          <Logo alt="" width={28} height={28} />
          <span>
            <Wordmark />
            <span className="brand-subtitle">Design system</span>
          </span>
        </a>
        <div className="nav-label">{t("DESIGN LIBRARY", "设计资源库")}</div>
        <nav aria-label={t("Design system", "设计系统")}>
          {pages.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={16} />
              {t(item.en, item.zh)}
            </a>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="version-dot" />
          {t("A shared foundation", "共同的界面基础")}
          <p>
            {t(
              "Calm surfaces. Thoughtful interactions.",
              "安静的界面，细致的交互。",
            )}
          </p>
        </div>
      </aside>
      <div className="showroom-panel">
        <header className="showroom-header">
          <div className="row">
            <Button
              variant="ghost"
              size="sm"
              icon={
                sidebar ? (
                  <PanelLeftClose size={16} />
                ) : (
                  <PanelLeftOpen size={16} />
                )
              }
              aria-label={t("Toggle navigation", "切换导航")}
              onClick={() => setSidebar(!sidebar)}
            />
            <span className="breadcrumb-muted">
              {t("Design system", "设计系统")}
            </span>
            <span className="breadcrumb-divider">/</span>
            <strong>{t(title.en, title.zh)}</strong>
          </div>
          <div className="header-controls">
            <CommandPalette
              trigger={
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Search size={15} />}
                  aria-label={t("Search commands", "搜索命令")}
                >
                  <span className="search-label">
                    {t("Search commands", "搜索命令")}
                  </span>
                  <Kbd aria-hidden="true">⌘ K</Kbd>
                </Button>
              }
              open={commands}
              onOpenChange={setCommands}
              label={t("Commands", "命令")}
              closeLabel={t("Close commands", "关闭命令")}
              placeholder={t("Where would you like to go?", "想要前往哪里？")}
              emptyMessage={t(
                "No commands found. Try “settings” or “theme”.",
                "未找到命令，试试“设置”或“主题”。",
              )}
              groups={[
                {
                  label: t("Navigate", "导航"),
                  options: pages.map((item) => ({
                    value: item.id,
                    label: t(item.en, item.zh),
                    icon: <item.icon size={16} />,
                    keywords: [item.en, item.zh],
                  })),
                },
                {
                  label: t("Appearance", "外观"),
                  options: [
                    {
                      value: "theme",
                      label: t("Toggle theme", "切换主题"),
                      description: dark
                        ? t("Switch to light", "切换浅色")
                        : t("Switch to dark", "切换深色"),
                      icon: dark ? <Sun size={16} /> : <Moon size={16} />,
                      keywords: ["theme", "主题"],
                    },
                  ],
                },
              ]}
              onSelect={(value) =>
                value === "theme" ? setDark(!dark) : (location.hash = value)
              }
            />
            <Select
              size="sm"
              variant="ghost"
              label={t("Preview language", "预览语言")}
              placeholder="Language"
              value={language}
              onValueChange={setLanguage}
              options={[
                { value: "en", label: "EN" },
                { value: "zh-CN", label: "中文" },
              ]}
            />
            <Button
              variant="ghost"
              size="sm"
              icon={dark ? <Sun size={16} /> : <Moon size={16} />}
              aria-label={t("Toggle theme", "切换主题")}
              onClick={() => setDark(!dark)}
            />
            <Button
              variant="ghost"
              size="sm"
              icon={narrow ? <Monitor size={16} /> : <Smartphone size={16} />}
              aria-pressed={narrow}
              aria-label={t("Narrow preview", "窄屏预览")}
              onClick={() => setNarrow(!narrow)}
            />
          </div>
        </header>
        <main
          id="showcase-content"
          tabIndex={-1}
          className="showcase-content"
          data-narrow={narrow}
        >
          {page === "foundations" && (
            <>
              <div className="page-intro">
                <div className="eyebrow">A13N / FOUNDATIONS</div>
                <h1>{t("Clarity, by design.", "清晰，源于设计。")}</h1>
                <p>
                  {t(
                    "A quiet visual language for focused work.",
                    "为专注工作建立安静的视觉语言。",
                  )}
                </p>
              </div>
              <Foundations t={t} />
            </>
          )}
          {page === "components" && (
            <>
              <div className="page-intro">
                <h1>
                  {t("Small details. Shared behavior.", "细节统一，交互一致。")}
                </h1>
                <p>
                  {t(
                    "Explore states, keyboard behavior, and overlays.",
                    "体验不同状态、键盘操作和浮层。",
                  )}
                </p>
              </div>
              <Controls t={t} />
            </>
          )}
          {page === "settings" && (
            <SettingsDemo
              t={t}
              language={language}
              setLanguage={setLanguage}
              dark={dark}
              setDark={setDark}
            />
          )}
          {page === "collection" && <CollectionDemo t={t} />}
          <footer className="showroom-footer">
            a13n-ui<span>·</span>
            {t("Interactive component library", "交互式组件库")}
          </footer>
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<Showcase />);
