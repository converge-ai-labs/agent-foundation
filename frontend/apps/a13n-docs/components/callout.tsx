import {
  Info,
  Lightbulb,
  SealWarning,
  Warning,
  WarningOctagon,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
import type { Locale } from "@/lib/i18n";
import type { ReactNode } from "react";

/** GitHub alert kinds, each on a surface tinted with its own hue. */
const kinds: Record<
  string,
  { label: string; icon: Icon; tone: string; surface: string }
> = {
  note: {
    label: "Note",
    icon: Info,
    tone: "text-(--info-foreground)",
    surface: "bg-(--info)/10",
  },
  tip: {
    label: "Tip",
    icon: Lightbulb,
    tone: "text-(--success-foreground)",
    surface: "bg-(--success)/10",
  },
  important: {
    label: "Important",
    icon: SealWarning,
    tone: "text-violet-700 dark:text-violet-400",
    surface: "bg-violet-500/10",
  },
  warning: {
    label: "Warning",
    icon: Warning,
    tone: "text-(--warning-foreground)",
    surface: "bg-(--warning)/12",
  },
  caution: {
    label: "Caution",
    icon: WarningOctagon,
    tone: "text-(--destructive-foreground)",
    surface: "bg-(--destructive)/10",
  },
};

export function Callout({
  type = "note",
  locale = "en",
  children,
}: {
  type?: string;
  locale?: Locale;
  children: ReactNode;
}) {
  const kind = kinds[type] ?? kinds.note;
  const labels: Record<string, string> = {
    note: "说明",
    tip: "提示",
    important: "重要",
    warning: "注意",
    caution: "警告",
  };
  const label = locale === "zh-CN" ? (labels[type] ?? labels.note) : kind.label;
  const Icon = kind.icon;
  return (
    <div
      role="note"
      className={`my-6 rounded-xl px-4 py-3.5 text-sm ${kind.surface}`}
    >
      <div
        className={`mb-1 flex items-center gap-1.5 text-[13px] font-semibold ${kind.tone}`}
      >
        <Icon weight="duotone" className="size-4" />
        {label}
      </div>
      <div className="prose-no-margin">{children}</div>
    </div>
  );
}
