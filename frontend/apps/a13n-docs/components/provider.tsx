"use client";
import { RootProvider } from "fumadocs-ui/provider/next";
import { defineTranslations } from "fumadocs-core/i18n";
import { uiTranslations, i18nProvider } from "fumadocs-ui/i18n";
import { openapiTranslations } from "fumadocs-openapi/i18n";
import { useI18n } from "fumadocs-ui/contexts/i18n";
import type { ReactNode } from "react";
import { type Locale } from "@/lib/i18n";
import zhCN from "@/lib/locales/ui.zh-CN.json";
import SearchDialog from "./search";

const chinese = defineTranslations()
  .extend(uiTranslations())
  .extend(openapiTranslations())
  .add(zhCN);

export function useLocale(): Locale {
  return useI18n().locale === "zh-CN" ? "zh-CN" : "en";
}

export function Provider({
  children,
  locale,
}: {
  children: ReactNode;
  locale: Locale;
}) {
  return (
    <RootProvider
      i18n={{ ...(locale === "zh-CN" ? i18nProvider(chinese) : {}), locale }}
      search={{ SearchDialog }}
    >
      {children}
    </RootProvider>
  );
}
