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
const titles: Record<Page, string> = {
  root: "Agent & Model",
  model: "Select model",
  agent: "Select agent",
  thinking: "Thinking",
  speed: "Speed",
  reasoning: "Reasoning mode",
};
const SettingsContext = createContext<{
  page: Page;
  navigate: (page: Page) => void;
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
  const rootTitle = "Agent & Model";
  const navigationLabel = "Agent & Model settings";
  const [open, setOpen] = useState(false);
  const [page, navigate] = useState<Page>("root");
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );
  const heading = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 639px)");
    const update = () => setMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (open) heading.current?.focus();
  }, [page, open, mobile]);
  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) {
      navigate("root");
    }
  };
  const Title = mobile ? DialogTitle : PopoverTitle;
  const showHeader = mobile || page !== "root";
  const help =
    page === "thinking"
      ? "Higher thinking levels spend more time and tokens on a response. Available levels depend on the selected model. Default uses the model’s saved setting."
      : page === "speed"
        ? "Default uses the model’s saved setting. Faster modes depend on your provider and account, may consume more credits, and do not guarantee faster responses. Ultrafast requires an eligible plan and has higher usage rates."
        : undefined;
  const content = (
    <SettingsContext value={{ page, navigate }}>
      <div
        className={showHeader ? styles.header : styles.srOnly}
        ref={heading}
        tabIndex={-1}
      >
        {page !== "root" && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Back to ${navigationLabel}`}
            title={`Back to ${navigationLabel}`}
            onClick={() => navigate("root")}
          >
            <CaretLeft />
          </Button>
        )}
        <Title className={styles.title}>
          {page === "root" ? rootTitle : titles[page]}
        </Title>
        {help && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  tooltip={false}
                  aria-label={`About ${titles[page].toLowerCase()}`}
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
            aria-label={`Close ${navigationLabel}`}
            title={`Close ${navigationLabel}`}
            onClick={() => changeOpen(false)}
          >
            <X />
          </Button>
        )}
      </div>
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
        <span>{label ?? "Model"}</span>
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
        <span>{label ?? "Model"}</span>
        <CaretDown aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup side="top" align="end" className={styles.popup}>
        {content}
      </PopoverPopup>
    </Popover>
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
  label,
}: {
  options: readonly SearchOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label: string;
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
          aria-label={`Search ${label}`}
          placeholder={`Search ${label}…`}
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
                {item.label}
                {item.badge && <small>{item.badge}</small>}
              </span>
              {item.description && <small>{item.description}</small>}
            </span>
          </span>
          {value === item.value && <Check aria-hidden />}
        </Button>
      ))}
      {!filtered.length && <p>No matching {label}.</p>}
    </div>
  );
}
