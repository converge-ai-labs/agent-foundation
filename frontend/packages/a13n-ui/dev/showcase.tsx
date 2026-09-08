import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, Select, Switch } from "../src";
import "../src/styles/index.css";
import "./showcase.css";
import { Foundations } from "./foundations";
import { Controls } from "./controls";
export type Translate = (en: string, zh: string) => string;
function Showcase() {
  const [language, setLanguage] = useState("en");
  const [dark, setDark] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const t: Translate = (en, zh) => (language === "en" ? en : zh);
  useEffect(() => {
    document.documentElement.dataset.a13nTheme = dark ? "dark" : "light";
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  return (
    <main className="a13n-root showcase" data-narrow={narrow}>
      <header>
        <div>
          <p className="eyebrow">AGENT FOUNDATION / UI</p>
          <h1>{t("Design system", "设计系统")}</h1>
          <p>
            {t(
              "Quiet surfaces. Clear hierarchy. Predictable interactions.",
              "安静的界面、清晰的层级、可预期的交互。",
            )}
          </p>
        </div>
        <div className="toolbar">
          <Select
            label={t("Language", "语言")}
            placeholder="Language"
            value={language}
            onValueChange={setLanguage}
            options={[
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
            ]}
          />
          <Switch
            label={t("Dark theme", "深色主题")}
            checked={dark}
            onCheckedChange={setDark}
          />
          <Button onClick={() => setNarrow(!narrow)}>
            {narrow
              ? t("Full width", "完整宽度")
              : t("Narrow preview", "窄屏预览")}
          </Button>
        </div>
      </header>
      <Foundations t={t} />
      <Controls t={t} />
      <footer>
        {t(
          "Development showcase · Uses the public a13n-ui components",
          "开发展示页 · 使用 a13n-ui 公共组件",
        )}
      </footer>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Showcase />);
