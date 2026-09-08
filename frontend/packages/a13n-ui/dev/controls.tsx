import { useState } from "react";
import { Globe, Plus } from "lucide-react";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  Input,
  Select,
  Spinner,
  Switch,
  Tooltip,
} from "../src";
import type { Translate } from "./showcase";
export function Controls({ t }: { t: Translate }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const options = [
    {
      value: "one",
      label: t("First option", "第一个选项"),
      icon: <Globe size={16} />,
    },
    { value: "two", label: t("Second option", "第二个选项") },
    { value: "disabled", label: t("Unavailable", "不可用"), disabled: true },
  ];
  return (
    <>
      <section>
        <h2>{t("Buttons & feedback", "按钮与反馈")}</h2>
        <div className="row">
          {(["primary", "secondary", "ghost", "danger"] as const).map(
            (variant) => (
              <Button variant={variant} key={variant}>
                {variant}
              </Button>
            ),
          )}
          <Button disabled>{t("Disabled", "禁用")}</Button>
          <Tooltip content={t("Add an item", "添加项目")}>
            <Button icon={<Plus size={16} />} aria-label={t("Add", "添加")} />
          </Tooltip>
        </div>
        <div className="row">
          <Button
            variant="primary"
            icon={<Plus size={16} />}
            loading={loading}
            loadingLabel={t("Saving…", "正在保存…")}
          >
            {t("Save changes", "保存更改")}
          </Button>
          <Switch
            label={t("Loading state", "加载状态")}
            checked={loading}
            onCheckedChange={setLoading}
          />
          <Spinner />
          <span className="secondary">
            {t(
              "Spinner is decorative; its owner supplies a label.",
              "旋转图标仅作装饰，由使用方提供文字。",
            )}
          </span>
        </div>
        <div className="row">
          {(["neutral", "success", "warning", "danger"] as const).map(
            (tone) => (
              <Badge key={tone} tone={tone}>
                {tone}
              </Badge>
            ),
          )}
        </div>
      </section>
      <section>
        <h2>{t("Fields & selection", "表单与选择")}</h2>
        <div className="columns">
          <div className="stack">
            <Input
              label={t("Display name", "显示名称")}
              placeholder={t("Enter a name", "输入名称")}
              hint={t("A short, recognizable label.", "简短且易于识别的名称。")}
              error={
                error
                  ? t("Enter a display name.", "请输入显示名称。")
                  : undefined
              }
            />
            <Switch
              label={t("Show validation error", "显示校验错误")}
              checked={error}
              onCheckedChange={setError}
            />
            <Input
              label={t("Disabled field", "禁用输入框")}
              disabled
              value={t("Read only example", "禁用示例")}
            />
          </div>
          <div className="stack">
            <Select
              label={t("Option", "选项")}
              placeholder={t("Choose an option", "选择一个选项")}
              options={options}
            />
            <Checkbox label={t("Enable this option", "启用此选项")} />
            <Checkbox
              label={t("Mixed selection", "部分选中")}
              checked="indeterminate"
            />
            <Switch label={t("Disabled switch", "禁用开关")} disabled />
          </div>
        </div>
      </section>
      <section>
        <h2>{t("Overlays", "浮层")}</h2>
        <p>
          {t(
            "Try Tab, arrow keys and Escape. Closing the dialog returns focus to its trigger.",
            "试试 Tab、方向键和 Escape，关闭对话框后焦点返回触发按钮。",
          )}
        </p>
        <Dialog
          trigger={<Button>{t("Open dialog", "打开对话框")}</Button>}
          title={t("Edit preferences", "编辑偏好")}
          description={t(
            "A focused surface for a small decision.",
            "在聚焦的界面里完成一个小决定。",
          )}
          closeLabel={t("Close", "关闭")}
        >
          <div className="stack">
            <Input label={t("Name", "名称")} />
            <Select
              label={t("Option", "选项")}
              placeholder={t("Choose an option", "选择一个选项")}
              options={options}
            />
          </div>
        </Dialog>
      </section>
    </>
  );
}
