import { TooltipProvider } from "../src";
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Moon, Sun } from "lucide-react";
import { Button, ChoiceField, Logo, Wordmark, cn } from "../src";
import { Foundations } from "./foundations";
import { Controls } from "./controls";
import { SettingsDemo } from "./settings-demo";
import { CollectionDemo } from "./collection-demo";
import "../src/styles/index.css";
export type Translate = (en: string, zh: string) => string;
const pages = [
  ["foundations", "Foundations", "基础规范"],
  ["components", "Components", "基础组件"],
  ["settings", "Settings", "设置场景"],
  ["collection", "Collection", "集合场景"],
];
function currentPage() {
  return pages.some(([id]) => id === location.hash.slice(1))
    ? location.hash.slice(1)
    : "settings";
}
function Showcase() {
  const [page, setPage] = useState(currentPage);
  const [language, setLanguage] = useState("en");
  const [dark, setDark] = useState(false);
  const t: Translate = (en, zh) => (language === "en" ? en : zh);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  useEffect(() => {
    const change = () => setPage(currentPage());
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  return (
    <div className="a13n-root min-h-svh bg-background">
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-4 border-b bg-background px-4 py-3 sm:px-8">
        <a href="#settings" className="flex items-center gap-2">
          <Logo alt="" width={28} height={28} />
          <Wordmark />
        </a>
        <span className="text-sm text-muted-foreground">Design system</span>
        <div className="ml-auto flex items-center gap-2">
          <ChoiceField
            label={t("Preview language", "预览语言")}
            hideLabel
            value={language}
            onValueChange={setLanguage}
            options={[
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
            ]}
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("Toggle theme", "切换主题")}
            onClick={() => setDark(!dark)}
          >
            {dark ? <Sun /> : <Moon />}
          </Button>
        </div>
      </header>
      <div className="mx-auto grid max-w-6xl gap-8 p-4 sm:grid-cols-[180px_minmax(0,1fr)] sm:p-8">
        <nav
          aria-label={t("Design system", "设计系统")}
          className="flex gap-1 overflow-x-auto sm:flex-col"
        >
          {pages.map(([id, en, zh]) => (
            <a
              key={id}
              href={`#${id}`}
              aria-current={id === page ? "page" : undefined}
              className={cn(
                "whitespace-nowrap rounded-lg px-3 py-2 text-sm hover:bg-accent",
                id === page && "bg-accent font-medium",
              )}
            >
              {t(en, zh)}
            </a>
          ))}
        </nav>
        <main id="showcase-content" className="min-w-0 flex flex-col gap-8">
          {page === "foundations" && <Foundations t={t} />}
          {page === "components" && <Controls t={t} />}
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
          <footer className="border-t py-6 text-xs text-muted-foreground">
            a13n-ui · Coss UI
          </footer>
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <TooltipProvider>
    <Showcase />
  </TooltipProvider>,
);
