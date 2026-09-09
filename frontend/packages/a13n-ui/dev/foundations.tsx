import { Logo, Wordmark } from "../src";
import type { Translate } from "./showcase";
const colors = [
  "app",
  "canvas",
  "elevated",
  "surface",
  "text",
  "secondary",
  "muted",
  "border",
  "selected",
  "accent",
  "success",
  "warning",
  "danger",
];
export function Foundations({ t }: { t: Translate }) {
  return (
    <>
      <section>
        <h2>{t("Brand", "品牌")}</h2>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            fontSize: 48,
          }}
        >
          <Logo alt="" width={48} height={48} />
          <Wordmark />
        </div>
        <p>
          {t(
            "Space Grotesk Bold. Use Wordmark for the a13n name across product surfaces.",
            "Space Grotesk Bold。产品中的 a13n 名称统一使用 Wordmark。",
          )}
        </p>
      </section>
      <section>
        <h2>{t("Foundations", "基础规范")}</h2>
        <p>
          {t(
            "Use color to clarify meaning; use space before adding borders. Keep secondary text readable and focus visible.",
            "颜色传达含义，优先用留白划分区域。辅助文字应可读，焦点应清晰。",
          )}
        </p>
        <div className="swatches">
          {colors.map((color) => (
            <div key={color}>
              <span
                className="swatch"
                style={{ background: `var(--a13n-${color})` }}
              />
              <code>{color}</code>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2>{t("Type & rhythm", "字体与节奏")}</h2>
        <div className="columns">
          <div>
            {["title", "heading", "body", "control", "sm", "xs"].map((size) => (
              <p key={size} style={{ fontSize: `var(--a13n-text-${size})` }}>
                {size} · {t("A clear place to begin", "从清晰的起点开始")}
              </p>
            ))}
          </div>
          <div>
            <p className="secondary">
              {t(
                "Navigation uses compact rows. Forms, toolbars, and settings share a spacing scale and use controls sized for their context.",
                "导航使用紧凑行高；表单、工具栏和设置共用间距尺度，并按使用场景选择控件尺寸。",
              )}
            </p>
            {[1, 2, 3, 4, 5, 6, 8, 12].map((space) => (
              <div className="space-row" key={space}>
                <code>space-{space}</code>
                <span style={{ width: `var(--a13n-space-${space})` }} />
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
