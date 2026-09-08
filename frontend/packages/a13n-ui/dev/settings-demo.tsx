import { useState } from "react";
import { Globe, Folder, FileText, Lock } from "lucide-react";
import { Picker, Select, SettingsRow, SettingsSection, Switch } from "../src";
import type { Translate } from "./showcase";
interface SettingsDemoProps {
  t: Translate;
  language: string;
  setLanguage: (value: string) => void;
  dark: boolean;
  setDark: (value: boolean) => void;
}
export function SettingsDemo({
  t,
  language,
  setLanguage,
  dark,
  setDark,
}: SettingsDemoProps) {
  const [location, setLocation] = useState("personal");
  const [compact, setCompact] = useState(false);
  return (
    <div className="settings-demo">
      <div className="page-intro">
        <h1>{t("Preferences", "偏好设置")}</h1>
        <p>
          {t(
            "Small choices that make a space feel like yours.",
            "让工作空间更合心意。",
          )}
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
          <Select
            id="settings-language"
            aria-describedby="settings-language-description"
            size="sm"
            label={t("Language", "语言")}
            placeholder={t("Choose a language", "选择语言")}
            value={language}
            onValueChange={setLanguage}
            options={[
              { value: "en", label: "English", icon: <Globe size={16} /> },
              { value: "zh-CN", label: "简体中文", icon: <Globe size={16} /> },
            ]}
          />
        </SettingsRow>
        <SettingsRow
          label={t("Default location", "默认位置")}
          description={t(
            "Search by name, description, or collection.",
            "按名称、描述或所属集合搜索。",
          )}
          controlId="settings-location"
        >
          <Picker
            id="settings-location"
            aria-describedby="settings-location-description"
            label={t("Default location", "默认位置")}
            placeholder={t("Choose a location", "选择位置")}
            emptyMessage={t(
              "No locations found. Try a different name.",
              "未找到位置，请换个名称试试。",
            )}
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
                    icon: <Folder size={16} />,
                  },
                ],
              },
              {
                label: t("Workspace", "工作空间"),
                options: [
                  {
                    value: "guides",
                    label: t("Guides", "指南"),
                    description: t("Workspace / Learning", "工作空间 / 学习"),
                    icon: <FileText size={16} />,
                  },
                  {
                    value: "reference",
                    label: t("Reference", "参考资料"),
                    description: t(
                      "Workspace / Reference",
                      "工作空间 / 参考资料",
                    ),
                    icon: <Folder size={16} />,
                  },
                  {
                    value: "restricted",
                    label: t("Restricted collection", "受限集合"),
                    description: t(
                      "Ask an owner for access",
                      "需要所有者授予访问权限",
                    ),
                    disabled: true,
                    icon: <Lock size={16} />,
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
            "A comfortable surface for low-light environments.",
            "适合光线较暗的环境。",
          )}
          controlId="settings-dark"
        >
          <Switch
            id="settings-dark"
            aria-describedby="settings-dark-description"
            label={t("Dark theme", "深色主题")}
            labelHidden
            checked={dark}
            onCheckedChange={setDark}
          />
        </SettingsRow>
        <SettingsRow
          label={t("Compact preview", "紧凑预览")}
          description={t(
            "Compare a compact row with the standard layout below.",
            "比较下方紧凑行与标准布局的差异。",
          )}
          controlId="settings-compact"
        >
          <Switch
            id="settings-compact"
            aria-describedby="settings-compact-description"
            label={t("Compact preview", "紧凑预览")}
            labelHidden
            checked={compact}
            onCheckedChange={setCompact}
          />
        </SettingsRow>
      </SettingsSection>
      <div className="density-sample" data-compact={compact}>
        <FileText size={16} />
        <div>
          <strong>
            {t("A place for clear thinking", "留一处清晰思考的空间")}
          </strong>
          <span>
            {t(
              "The content stays readable at both densities.",
              "两种密度都保持内容清晰可读。",
            )}
          </span>
        </div>
        <span className="muted">{t("Preview", "预览")}</span>
      </div>
      <SettingsSection
        title={t("Notifications", "通知")}
        description={t(
          "Explain dependencies before asking someone to change a setting.",
          "先说明依赖条件，再让用户调整设置。",
        )}
      >
        <SettingsRow
          label={t("Priority notifications", "优先通知")}
          description={t(
            "Connect a notification channel before enabling this option.",
            "连接通知渠道后即可启用此选项。",
          )}
          controlId="settings-notifications"
        >
          <Switch
            id="settings-notifications"
            aria-describedby="settings-notifications-description"
            label={t("Priority notifications", "优先通知")}
            labelHidden
            disabled
          />
        </SettingsRow>
      </SettingsSection>
    </div>
  );
}
