import { Logo, Wordmark } from "../src";
import type { Translate } from "./showcase";
const colors = [
  "background",
  "foreground",
  "muted",
  "muted-foreground",
  "accent",
  "primary",
  "border",
  "destructive",
];
export function Foundations({ t }: { t: Translate }) {
  return (
    <>
      <h1 className="text-2xl font-medium">{t("Foundations", "基础规范")}</h1>
      <section className="flex flex-col gap-4">
        <h2 className="font-medium">{t("Brand", "品牌")}</h2>
        <div className="flex items-center gap-4 text-5xl">
          <Logo alt="" width={48} height={48} />
          <Wordmark />
        </div>
        <p className="text-sm text-muted-foreground">Space Grotesk Bold</p>
      </section>
      <section className="flex flex-col gap-4">
        <h2 className="font-medium">{t("Semantic colors", "语义颜色")}</h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {colors.map((color) => (
            <div key={color} className="flex flex-col gap-2">
              <div
                className="h-16 rounded-lg border"
                style={{ background: `var(--${color})` }}
              />
              <code className="text-xs">{color}</code>
            </div>
          ))}
        </div>
      </section>
      <section className="flex flex-col gap-4">
        <h2 className="font-medium">{t("Typography", "字体")}</h2>
        <p className="text-3xl font-medium">
          {t("A clear place to begin", "从清晰的起点开始")}
        </p>
        <p>
          {t(
            "A shared language for focused work.",
            "为专注工作建立共同的界面语言。",
          )}
        </p>
        <p className="text-sm text-muted-foreground">
          {t(
            "Secondary text stays readable in both themes.",
            "辅助文字在两种主题下均保持清晰可读。",
          )}
        </p>
      </section>
    </>
  );
}
