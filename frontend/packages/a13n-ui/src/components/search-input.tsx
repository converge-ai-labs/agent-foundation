import type { ComponentProps } from "react";
import { Search } from "lucide-react";
import styles from "./search-input.module.css";

export type SearchInputProps = Omit<ComponentProps<"input">, "type"> & {
  label: string;
};

/** A quiet, named search control for collection toolbars. */
export function SearchInput({
  label,
  className = "",
  ...props
}: SearchInputProps) {
  return (
    <div className={`${styles.search} ${className}`}>
      <Search size={15} strokeWidth={1.6} aria-hidden="true" />
      <input {...props} type="search" aria-label={label} />
    </div>
  );
}
