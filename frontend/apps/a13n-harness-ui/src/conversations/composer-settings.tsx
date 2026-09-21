import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type ComponentProps,
  type RefObject,
} from "react";
import {
  Button,
  Dialog,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  Input,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  type SearchPicker,
} from "a13n-ui";
import {
  CaretLeft,
  CaretRight,
  Check,
  SlidersHorizontal,
  X,
} from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

type SearchOption = ComponentProps<
  typeof SearchPicker
>["groups"][number]["options"][number];

type Page = "root" | "model" | "agent" | "execution" | "environments";
const titles: Record<Page, string> = {
  root: "Run settings",
  model: "Select model",
  agent: "Select agent",
  execution: "Execution mode",
  environments: "Working environments",
};
const SettingsContext = createContext<{
  page: Page;
  navigate: (page: Page) => void;
  environmentDraft?: Schema<"EnvironmentSelectionPatch">;
  setEnvironmentDraft: (
    value: Schema<"EnvironmentSelectionPatch"> | undefined,
  ) => void;
} | null>(null);
export const useComposerSettings = () => useContext(SettingsContext);

/** Container width, not device identity, decides which shortcuts fit. */
export function useCompactComposer(host: RefObject<HTMLElement | null>) {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const measure = (width: number) => {
      if (width > 0) setCompact(width <= 720);
    };
    measure(element.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) =>
      measure(entry.contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [host]);
  return compact;
}

export function ComposerSettings({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [page, navigate] = useState<Page>("root");
  // Keep staged edits above the responsive Dialog/Popover subtree.
  const [environmentDraft, setEnvironmentDraft] =
    useState<Schema<"EnvironmentSelectionPatch">>();
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
      setEnvironmentDraft(undefined);
    }
  };
  const Title = mobile ? DialogTitle : PopoverTitle;
  const content = (
    <SettingsContext
      value={{ page, navigate, environmentDraft, setEnvironmentDraft }}
    >
      <div className={styles.header} ref={heading} tabIndex={-1}>
        {page !== "root" && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Back to run settings"
            title="Back to run settings"
            onClick={() => navigate("root")}
          >
            <CaretLeft />
          </Button>
        )}
        <Title className={styles.title}>{titles[page]}</Title>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close run settings"
          title="Close run settings"
          onClick={() => changeOpen(false)}
        >
          <X />
        </Button>
      </div>
      <div className={styles.body}>{children}</div>
    </SettingsContext>
  );
  const trigger = (
    <Button variant="ghost" size="icon-sm" className={className} />
  );
  const triggerProps = {
    "aria-label": "Composer options",
    title: "Run settings",
    // Do not bring the mobile keyboard back when the panel closes.
    onClick: () => {
      if (document.activeElement instanceof HTMLElement)
        document.activeElement.blur();
    },
  };
  return mobile ? (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger render={trigger} {...triggerProps}>
        <SlidersHorizontal />
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
        <SlidersHorizontal />
      </PopoverTrigger>
      <PopoverPopup side="top" align="end" className={styles.popup}>
        {content}
      </PopoverPopup>
    </Popover>
  );
}

export function SettingsHome({ children }: { children: ReactNode }) {
  return useComposerSettings()?.page === "root" ? <>{children}</> : null;
}

export function SettingsRow({
  label,
  value,
  disabled,
  onClick,
}: {
  label: string;
  value?: string;
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
          aria-pressed={value === item.value}
          onClick={() => onChange(item.value)}
        >
          <span>
            {item.label}
            {item.badge && <small>{item.badge}</small>}
            {item.description && <small>{item.description}</small>}
          </span>
          {value === item.value && <Check aria-hidden />}
        </Button>
      ))}
      {!filtered.length && <p>No matching {label}.</p>}
    </div>
  );
}
