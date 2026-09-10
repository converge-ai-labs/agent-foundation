import { useState } from "react";
import { Plus, X } from "lucide-react";
import {
  Badge,
  Button,
  Calendar,
  Checkbox,
  ChoiceField,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  DisclosureSection,
  FormField,
  Input,
  Label,
  ModalFrame,
  ScrollArea,
  Switch,
  Textarea,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "../src";
import type { Translate } from "./showcase";
export function Controls({ t }: { t: Translate }) {
  const [loading, setLoading] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [date, setDate] = useState<Date>();
  const [adding, setAdding] = useState(false);
  const [channel, setChannel] = useState(false);
  const [retries, setRetries] = useState("3");
  const [delay, setDelay] = useState("5");
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
        <h2 className="font-semibold">{t("Configuration", "配置")}</h2>
        <div className="rounded-lg bg-muted/50 p-4">
          <FormField
            className="flex flex-row flex-wrap items-center gap-3"
            label={t("Pinned version", "固定版本")}
          >
            <Input type="number" min={1} defaultValue={1} className="w-20" />
          </FormField>
        </div>
        <DisclosureSection
          title={t("Retry settings", "重试设置")}
          summary={`${retries} ${t("attempts", "次尝试")}`}
        >
          <FormField label={t("Maximum attempts", "最大尝试次数")}>
            <Input
              type="number"
              min={1}
              value={retries}
              onChange={(event) => setRetries(event.target.value)}
              className="max-w-28"
            />
          </FormField>
          <FormField label={t("Retry delay (seconds)", "重试间隔（秒）")}>
            <Input
              type="number"
              min={0}
              defaultValue={5}
              className="max-w-28"
            />
          </FormField>
        </DisclosureSection>
        <Collapsible
          open={adding}
          onOpenChange={setAdding}
          className="rounded-lg data-open:bg-muted/50 data-open:p-3"
        >
          <CollapsibleTrigger render={<Button variant="secondary" size="sm" />}>
            {adding ? <X /> : <Plus />}
            {adding
              ? t("Close selection", "关闭选择")
              : t("Add channel", "添加渠道")}
          </CollapsibleTrigger>
          <CollapsiblePanel>
            <Label className="flex items-center gap-2 px-1 pt-4 pb-1">
              <Checkbox checked={channel} onCheckedChange={setChannel} />
              {t("Activity feed", "动态列表")}
            </Label>
          </CollapsiblePanel>
        </Collapsible>
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
      <section className="grid gap-6 sm:grid-cols-2">
        <FormField label={t("Scrollable input", "可滚动输入框")}>
          <Textarea
            rows={6}
            defaultValue={Array.from(
              { length: 24 },
              (_, index) =>
                `${index + 1}. ${t("Keep long content inside the editor.", "长内容在编辑器内部滚动。")}`,
            ).join("\n")}
          />
        </FormField>
        <div className="min-w-0">
          <h2 className="mb-2 text-sm font-medium">
            {t("Scroll area", "滚动区域")}
          </h2>
          <ScrollArea
            className="h-40 rounded-lg bg-muted/50"
            overscrollContain
            scrollbarGutter
          >
            <div className="grid gap-3 p-4">
              {Array.from({ length: 16 }, (_, index) => (
                <p key={index} className="text-sm">
                  {t("Item", "条目")} {index + 1}
                </p>
              ))}
            </div>
          </ScrollArea>
        </div>
      </section>
    </>
  );
}
