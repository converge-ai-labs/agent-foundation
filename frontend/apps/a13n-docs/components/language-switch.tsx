"use client";

import { CaretDown, Translate } from "@phosphor-icons/react/dist/ssr";
import {
  Menu,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
} from "a13n-ui";
import { usePathname, useRouter } from "next/navigation";
import { localeUrl, messages, type Locale } from "@/lib/i18n";
import { useLocale } from "./provider";

const languages = {
  en: { label: "English", compact: "EN" },
  "zh-CN": { label: "简体中文", compact: "中文" },
} satisfies Record<Locale, { label: string; compact: string }>;

export function LanguageSwitch() {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const current = languages[locale];

  return (
    <Menu>
      <MenuTrigger
        aria-label={`${messages[locale].language} · ${current.label}`}
        className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[13px] text-fd-muted-foreground transition-colors duration-150 hover:bg-fd-accent hover:text-fd-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fd-ring data-popup-open:bg-fd-accent data-popup-open:text-fd-foreground"
      >
        <Translate className="size-[18px]" aria-hidden="true" />
        <span className="max-sm:hidden">{current.label}</span>
        <span className="sm:hidden">{current.compact}</span>
        <CaretDown
          className="size-3 text-fd-muted-foreground"
          aria-hidden="true"
        />
      </MenuTrigger>
      <MenuPopup align="end" sideOffset={8} className="w-32">
        <MenuRadioGroup
          aria-label={messages[locale].language}
          value={locale}
          onValueChange={(value) => {
            if (value !== "en" && value !== "zh-CN") return;
            if (value === locale) return;
            router.push(
              `${localeUrl(pathname, value)}${window.location.search}${window.location.hash}`,
            );
          }}
        >
          {Object.entries(languages).map(([value, language]) => (
            <MenuRadioItem
              key={value}
              value={value}
              closeOnClick
              className="min-h-10 cursor-pointer rounded-lg text-[13px] sm:min-h-10 sm:text-[13px] data-checked:bg-accent data-checked:font-medium"
            >
              <span lang={value}>{language.label}</span>
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
      </MenuPopup>
    </Menu>
  );
}
