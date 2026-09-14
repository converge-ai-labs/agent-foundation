import { useState } from "react";
import {
  ChoiceField,
  SearchPicker,
  SettingsRow,
  SettingsSection,
  Switch,
} from "../src";
import type { Translate } from "./showcase";
export function SettingsDemo({
  t,
  language,
  setLanguage,
  dark,
  setDark,
}: {
  t: Translate;
  language: string;
  setLanguage: (value: string) => void;
  dark: boolean;
  setDark: (value: boolean) => void;
}) {
  const [location, setLocation] = useState("personal");
  return (
    <>
      <div>
        <h1 className="text-2xl font-medium">{t("Preferences", "偏好设置")}</h1>
        <p className="mt-2 text-muted-foreground">
          {t("Make this space your own.", "让工作空间更合心意。")}
        </p>
      </div>
      <SettingsSection title={t("General", "通用")}>
        <SettingsRow
          label={t("Language", "语言")}
          description={t(
            "Choose the language used throughout this preview.",
            "选择展示页使用的语言。",
          )}
          controlId="settings-language"
        >
          <ChoiceField
            id="settings-language"
            label={t("Language", "语言")}
            hideLabel
            value={language}
            onValueChange={setLanguage}
            options={[
              { value: "en", label: "English" },
              { value: "zh-CN", label: "简体中文" },
            ]}
          />
        </SettingsRow>
        <SettingsRow
          label={t("Default location", "默认位置")}
          description={t(
            "Search by name or collection.",
            "按名称或所属集合搜索。",
          )}
          controlId="settings-location"
        >
          <SearchPicker
            id="settings-location"
            label={t("Default location", "默认位置")}
            placeholder={t("Choose a location", "选择位置")}
            emptyMessage={t("No locations found.", "未找到位置。")}
            value={location}
            onValueChange={setLocation}
            groups={[
              {
                label: t("Personal", "个人"),
                options: [
                  {
                    value: "personal",
                    label: t("My space", "我的空间"),
                    description: t(
                      "Private notes and drafts",
                      "私人笔记和草稿",
                    ),
                  },
                ],
              },
              {
                label: t("Workspace", "工作空间"),
                options: [
                  {
                    value: "guides",
                    label: t("Guides", "指南"),
                    keywords: ["learning"],
                  },
                  {
                    value: "restricted",
                    label: t("Restricted collection", "受限集合"),
                    disabled: true,
                  },
                ],
              },
            ]}
          />
        </SettingsRow>
      </SettingsSection>
      <SettingsSection title={t("Appearance", "外观")}>
        <SettingsRow
          label={t("Dark theme", "深色主题")}
          description={t(
            "Use a darker surface in low light.",
            "在光线较暗时使用深色界面。",
          )}
          controlId="settings-dark"
        >
          <Switch id="settings-dark" checked={dark} onCheckedChange={setDark} />
        </SettingsRow>
      </SettingsSection>
      <SettingsSection title={t("Notifications", "通知")}>
        <SettingsRow
          label={t("Priority notifications", "优先通知")}
          description={t(
            "Connect a notification channel to enable this option.",
            "连接通知渠道后即可启用此选项。",
          )}
          controlId="settings-notifications"
        >
          <Switch id="settings-notifications" disabled />
        </SettingsRow>
      </SettingsSection>
    </>
  );
}
