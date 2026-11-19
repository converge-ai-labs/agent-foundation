/** Public prebuilt ESM interface. Import this through @a13n/console. */
import * as React from "react";
export { React };
export {
  createElement,
  createContext,
  useContext,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
  useId,
  lazy,
  Suspense,
  Fragment,
} from "react";
export type { ReactNode, ComponentType } from "react";
export {
  useQuery,
  useMutation,
  useQueryClient,
  useInfiniteQuery,
  queryOptions,
  infiniteQueryOptions,
} from "@tanstack/react-query";
export {
  Link,
  NavLink,
  Outlet,
  useNavigate,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router";
export { useTranslation, Trans } from "react-i18next";
export {
  Button,
  Input,
  Textarea,
  Switch,
  Checkbox,
  Select,
  SelectButton,
  SelectTrigger,
  SelectValue,
  SelectPopup,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectGroup,
  SelectGroupLabel,
  SelectLabel,
  ChoiceField,
  SearchPicker,
  SegmentedControl,
  FormField,
  ReadOnlyField,
  Field,
  FieldLabel,
  FieldItem,
  FieldDescription,
  FieldError,
  FieldControl,
  FieldValidity,
  Label,
  Badge,
  useToast,
  type SelectButtonProps,
  type SegmentedControlOption,
  type SegmentedControlProps,
} from "a13n-ui";
export { Page } from "../shared/page";
export { Empty } from "../shared/collection";
export { Loading, ErrorPage, ErrorToast } from "../shared/feedback";
export { ApiError, ProtocolError } from "../service-client";
export { useAuth } from "../auth/context";
export { useAccess, useWorkspace } from "../layout/workspace";
export { useAppearance } from "../layout/appearance";
export { useApp } from "./context";
export type { AppContribution, AppPage, AppSettings, AppText } from "./types";
export const bridgeVersion = 1;
