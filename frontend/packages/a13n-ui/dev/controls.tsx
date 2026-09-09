import { useState } from "react";
import { Plus } from "lucide-react";
import {
  Badge,
  Button,
  Calendar,
  Checkbox,
  ChoiceField,
  FormField,
  Input,
  Label,
  ModalFrame,
  Switch,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "../src";
import type { Translate } from "./showcase";
export function Controls({ t }: { t: Translate }) {
  const [loading, setLoading] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [date, setDate] = useState<Date>();
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold">
          {t("Components", "基础组件")}
        </h1>
        <p className="mt-2 text-muted-foreground">
          {t(
            "Shared controls, focus behavior, and feedback.",
            "统一的控件、焦点行为与反馈。",
          )}
        </p>
      </div>
      <section className="flex flex-col gap-4">
        <h2 className="font-semibold">
          {t("Buttons & feedback", "按钮与反馈")}
        </h2>
        <div className="flex flex-wrap gap-2">
          {(
            ["default", "outline", "secondary", "ghost", "destructive"] as const
          ).map((variant) => (
            <Button key={variant} variant={variant}>
              {variant}
            </Button>
          ))}
          <Button disabled>{t("Disabled", "禁用")}</Button>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  size="icon"
                  variant="outline"
                  aria-label={t("Add", "添加")}
                />
              }
            >
              <Plus />
            </TooltipTrigger>
            <TooltipPopup>{t("Add an item", "添加项目")}</TooltipPopup>
          </Tooltip>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button loading={loading}>{t("Save changes", "保存更改")}</Button>
          <Label>
            <Switch checked={loading} onCheckedChange={setLoading} />
            {t("Loading", "加载中")}
          </Label>
          {(["secondary", "default", "outline", "destructive"] as const).map(
            (variant) => (
              <Badge key={variant} variant={variant}>
                {variant}
              </Badge>
            ),
          )}
        </div>
      </section>
      <section className="grid gap-6 sm:grid-cols-2">
        <FormField
          label={t("Name", "名称")}
          description={t("Use a recognizable name.", "使用容易辨认的名称。")}
          error={invalid ? t("A name is required.", "请填写名称。") : undefined}
        >
          <Input required />
        </FormField>
        <ChoiceField
          label={t("Location", "位置")}
          placeholder={t("Choose a location", "选择位置")}
          options={[
            { value: "one", label: t("Workspace", "工作空间") },
            { value: "two", label: t("Personal", "个人") },
            {
              value: "disabled",
              label: t("Unavailable", "不可用"),
              disabled: true,
            },
          ]}
        />
        <Label>
          <Checkbox checked={invalid} onCheckedChange={setInvalid} />
          {t("Show validation error", "显示校验错误")}
        </Label>
      </section>
      <section className="flex flex-col gap-4">
        <h2 className="font-semibold">
          {t("Dialogs & calendar", "弹窗与日历")}
        </h2>
        <ModalFrame
          trigger={
            <Button variant="outline">{t("Open dialog", "打开弹窗")}</Button>
          }
          title={t("Preferences", "偏好设置")}
          description={t(
            "Changes stay in this preview.",
            "更改仅保留在当前预览中。",
          )}
          closeLabel={t("Close", "关闭")}
        >
          <FormField label={t("Name", "名称")}>
            <Input />
          </FormField>
          <div className="mt-4">
            <ModalFrame
              trigger={
                <Button variant="outline">
                  {t("Open nested dialog", "打开嵌套弹窗")}
                </Button>
              }
              title={t("Details", "详情")}
              closeLabel={t("Close details", "关闭详情")}
            >
              <Input aria-label={t("Note", "备注")} />
            </ModalFrame>
          </div>
        </ModalFrame>
        <div className="w-fit rounded-xl border">
          <Calendar mode="single" selected={date} onSelect={setDate} />
        </div>
      </section>
    </>
  );
}
