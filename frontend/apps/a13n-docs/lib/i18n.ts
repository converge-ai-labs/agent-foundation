import { defineI18n } from "fumadocs-core/i18n";

export const i18n = defineI18n({
  languages: ["en", "zh-CN"],
  defaultLanguage: "en",
  hideLocale: "default-locale",
  fallbackLanguage: null,
});
export type Locale = (typeof i18n.languages)[number];

/** Only human pages have locale prefixes; reference downloads keep their URLs. */
export function localeUrl(url: string, locale: Locale) {
  if (!url.startsWith("/") || url.startsWith("//")) return url;
  const path = url.replace(/^\/zh-CN(?=\/|$|[?#])/, "") || "/";
  if (
    /^\/(?:api|md|reference|_next)(?:\/|$)/.test(path) ||
    /^\/(?:llms(?:-full)?\.txt|LICENSE\.coss)(?:[?#]|$)/.test(path)
  )
    return path;
  return locale === "en"
    ? path
    : `/zh-CN${path.startsWith("/") ? path : `/${path}`}`;
}

export const messages = {
  en: {
    description:
      "The open-source, self-hosted foundation for AI agents: the Harness library, the managed Service, and the Harness UI workbench.",
    home: "a13n docs home",
    language: "Choose a language",
    theme: "Toggle dark theme",
    github: "GitHub repository",
    navigation: "Open navigation",
    pages: "Pages",
    next: "Next",
    previous: "Previous",
    open: "Open",
    openGitHub: "Open in GitHub",
    markdown: "View as Markdown",
    openChatGPT: "Open in ChatGPT",
    openClaude: "Open in Claude",
    openCursor: "Open in Cursor",
    prompt: "Read {url}, I want to ask questions about it.",
    getStarted: "Get started",
    overview: "Overview",
    notFound: "Page not found",
    notFoundDescription:
      "This page does not exist or has moved. Start again from a section below, or search the docs.",
  },
  "zh-CN": {
    description:
      "开源、可自托管的 AI agent 基础：用 Harness 在应用中运行 agent，用 Service 管理执行，或通过 Harness UI 交互式工作。",
    home: "a13n 文档首页",
    language: "选择语言",
    theme: "切换深色模式",
    github: "GitHub 仓库",
    navigation: "打开导航",
    pages: "文档导航",
    next: "下一篇",
    previous: "上一篇",
    open: "打开",
    openGitHub: "在 GitHub 中打开",
    markdown: "查看英文 Markdown",
    openChatGPT: "在 ChatGPT 中打开",
    openClaude: "在 Claude 中打开",
    openCursor: "在 Cursor 中打开",
    prompt: "请阅读 {url}，我想就其中的内容提问。",
    getStarted: "开始使用",
    overview: "概览",
    notFound: "找不到这个页面",
    notFoundDescription:
      "这个页面可能已被移除或更换了地址。可以从下方入口继续浏览，也可以搜索文档。",
  },
} satisfies Record<Locale, Record<string, string>>;
