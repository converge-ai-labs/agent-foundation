import { useTranslation } from "../i18n";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type ComponentProps,
} from "react";
import {
  Button,
  Dialog,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  Input,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  type SearchPicker,
} from "a13n-ui";
import {
  Question,
  CaretLeft,
  CaretRight,
  Check,
  CaretDown,
  X,
} from "@phosphor-icons/react";
import styles from "./composer-settings.module.css";

type SearchOption = ComponentProps<
  typeof SearchPicker
>["groups"][number]["options"][number];

type Page = "root" | "model" | "agent" | "thinking" | "speed" | "reasoning";
const SettingsContext = createContext<{
  page: Page;
  navigate: (page: Page) => void;
  mobile: boolean;
  close: () => void;
  open: boolean;
} | null>(null);
export const useComposerSettings = () => useContext(SettingsContext);

export function ComposerSettings({
  children,
  className,
  label,
}: {
  children: ReactNode;
  className?: string;
  label?: ReactNode;
}) {
  const { t } = useTranslation();
  const titles: Record<Page, string> = {
    root: t("Agent & Model"),
    model: t("Select model"),
    agent: t("Select agent"),
    thinking: t("Thinking"),
    speed: t("Speed"),
    reasoning: t("Reasoning mode"),
  };
  const rootTitle = titles.root;
  const navigationLabel = t("Agent & Model settings");
  const [open, setOpen] = useState(false);
  const [page, navigate] = useState<Page>("root");
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(max-width: 639px)");
    const update = () => setMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const changeOpen = (next: boolean) => {
    if (next) navigate("root");
    setOpen(next);
  };
  const content = (
    <SettingsContext
      value={{ page, navigate, mobile, open, close: () => changeOpen(false) }}
    >
      {page !== "thinking" && <SettingsHeader title={titles[page]} />}
      <div className={styles.body}>{children}</div>
    </SettingsContext>
  );
  const trigger = (
    <Button
      variant="ghost"
      size="sm"
      className={`${styles.modelTrigger} ${className ?? ""}`}
    />
  );
  const triggerProps = {
    "aria-label": navigationLabel,
    title: rootTitle,
    // Do not bring the mobile keyboard back when the panel closes.
    onClick: () => {
      if (document.activeElement instanceof HTMLElement)
        document.activeElement.blur();
    },
  };
  return mobile ? (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger render={trigger} {...triggerProps}>
        <span>{label ?? t("Model")}</span>
        <CaretDown aria-hidden="true" />
      </DialogTrigger>
      <DialogPopup
        bottomStickOnMobile
        showCloseButton={false}
        className={styles.sheet}
      >
        {content}
      </DialogPopup>
    </Dialog>
  ) : (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger render={trigger} {...triggerProps}>
        <span>{label ?? t("Model")}</span>
        <CaretDown aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="end"
        data-page={page}
        className={styles.popup}
      >
        {content}
      </PopoverPopup>
    </Popover>
  );
}

export function SettingsHeader({
  title,
  accessibleTitle,
  action,
}: {
  title: ReactNode;
  accessibleTitle?: string;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const settings = useComposerSettings();
  const { page, mobile, open, navigate, close } = settings ?? {};
  const heading = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) heading.current?.focus();
  }, [page, open, mobile]);
  const Title = !settings ? "h3" : mobile ? DialogTitle : PopoverTitle;
  const showHeader = mobile || page !== "root";
  const help =
    page === "speed"
      ? t(
          "Default uses the model’s saved setting. Faster modes depend on your provider and account, may consume more credits, and do not guarantee faster responses. Ultrafast requires an eligible plan and has higher usage rates.",
        )
      : undefined;
  return (
    <div
      className={showHeader ? styles.header : styles.srOnly}
      ref={heading}
      tabIndex={-1}
    >
      {navigate && page !== "root" && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Back to Agent & Model settings")}
          title={t("Back to Agent & Model settings")}
          onClick={() => navigate("root")}
        >
          <CaretLeft />
        </Button>
      )}
      <Title className={styles.title} aria-label={accessibleTitle}>
        {title}
      </Title>
      {action}
      {help && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                tooltip={false}
                aria-label={t("About speed")}
              />
            }
          >
            <Question />
          </TooltipTrigger>
          <TooltipPopup>{help}</TooltipPopup>
        </Tooltip>
      )}
      {mobile && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Close Agent & Model settings")}
          title={t("Close Agent & Model settings")}
          onClick={close}
        >
          <X />
        </Button>
      )}
    </div>
  );
}

export function SettingsRow({
  label,
  value,
  disabled,
  onClick,
}: {
  label: string;
  value?: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      className={styles.row}
      disabled={disabled}
      aria-label={label}
      onClick={onClick}
    >
      <span>{label}</span>
      <span className={styles.value}>{value}</span>
      <CaretRight aria-hidden />
    </Button>
  );
}

/** In-panel choices share the same catalog data as the desktop pickers. */
export function SettingsChoices({
  options,
  value,
  onChange,
  disabled,
  searchLabel,
  searchPlaceholder,
  emptyLabel,
}: {
  options: readonly SearchOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  searchLabel: string;
  searchPlaceholder: string;
  emptyLabel: string;
}) {
  const [query, setQuery] = useState("");
  const filtered = options.filter((item) =>
    [item.label, item.description, item.badge, ...(item.keywords ?? [])]
      .join(" ")
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <div className={styles.choices}>
      {options.length > 8 && (
        <Input
          aria-label={searchLabel}
          placeholder={searchPlaceholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      {filtered.map((item) => (
        <Button
          key={item.value}
          variant="ghost"
          className={styles.choice}
          disabled={disabled || item.disabled}
          title={item.description}
          aria-pressed={value === item.value}
          onClick={() => onChange(item.value)}
        >
          <span className={styles.choiceLabel}>
            {item.icon}
            <span className={styles.choiceText}>
              <span>
                <span>{item.label}</span>
                {item.badge && <small>{item.badge}</small>}
              </span>
              {item.description && <small>{item.description}</small>}
            </span>
          </span>
          {value === item.value && <Check aria-hidden />}
        </Button>
      ))}
      {!filtered.length && <p>{emptyLabel}</p>}
    </div>
  );
}
