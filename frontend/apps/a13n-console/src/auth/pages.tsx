import {
  ArrowLeftIcon,
  CheckCircleIcon,
  InfoIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  AlertAction,
  AlertDescription,
  Button,
  FormField,
  Input,
  Logo,
  Wordmark,
} from "a13n-ui";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
} from "react-router";

import { pairingSearch } from "../features/environments/pairing-link";
import { ApiError } from "../service-client";
import { data } from "../shared/api";
import { Loading } from "../shared/feedback";
import { useAuth, useClient } from "./context";
import styles from "./pages.module.css";

type Mode = "login" | "invite" | "forgot" | "reset" | "email";
type Translate = (value: string) => string;
/** A failure the reader can act on: beside the fields, or above the button. */
type Failure = { field?: boolean; message: string };

const passwordFieldId = "auth-password";
const passwordErrorId = `${passwordFieldId}-error`;

function rejectedFields(error: ApiError): string[] {
  const fields = error.details.fields;
  return Array.isArray(fields)
    ? fields.filter((field): field is string => typeof field === "string")
    : [];
}

/**
 * The Service answers a wrong email or password with 401
 * `authentication_required`, and rejects a password below its 15 character
 * minimum with 400 `invalid_request` naming the field. A failed fetch reaches
 * the mutation as a `TypeError` with no response at all.
 */
function readFailure(
  error: unknown,
  mode: Mode,
  t: Translate,
): Failure | undefined {
  if (!error) return undefined;
  if (error instanceof TypeError)
    return {
      message: t(
        "Can't reach the service. Check your connection and try again.",
      ),
    };
  if (!(error instanceof ApiError))
    return {
      message:
        error instanceof Error
          ? error.message
          : t("The request could not be completed."),
    };
  const rejected =
    error.status === 400 && error.code === "invalid_request"
      ? rejectedFields(error)
      : [];
  if (mode === "login" && (error.status === 401 || rejected.length > 0))
    return { field: true, message: t("Email or password is incorrect") };
  if (rejected.includes("password"))
    return {
      field: true,
      message: t("Choose a password with at least 15 characters."),
    };
  return { message: error.message };
}

function Notice({
  tone = "error",
  children,
  action,
}: {
  tone?: "error" | "info";
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Alert variant={tone} className={styles.notice}>
      {tone === "error" ? (
        <WarningCircleIcon aria-hidden="true" />
      ) : (
        <InfoIcon aria-hidden="true" />
      )}
      <AlertDescription>{children}</AlertDescription>
      {action && <AlertAction>{action}</AlertAction>}
    </Alert>
  );
}

export function AuthPage() {
  const client = useClient(),
    auth = useAuth(),
    location = useLocation(),
    navigate = useNavigate();
  const { invitationId } = useParams();
  const { t } = useTranslation();
  const mode: Mode = invitationId
    ? "invite"
    : location.pathname === "/forgot-password"
      ? "forgot"
      : location.pathname === "/reset-password"
        ? "reset"
        : location.pathname === "/confirm-email"
          ? "email"
          : "login";
  const [token] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "",
  );
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState("");
  const configuration = useQuery({
    queryKey: ["auth-configuration"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/auth/configuration", { signal }).then(data),
  });
  const mutation = useMutation({
    mutationFn: async () => {
      if (mode === "forgot") {
        await client.http.POST("/api/v1/auth/password-reset", {
          body: { email },
        });
        return;
      }
      if (mode === "reset") {
        await client.http.POST("/api/v1/auth/password-reset/complete", {
          body: { token, password },
        });
        return;
      }
      if (mode === "email") {
        await client.http.POST("/api/v1/users/me/email-change/complete", {
          body: { token },
        });
        await auth.refresh();
        return;
      }
      const result =
        mode === "invite" && invitationId
          ? data(
              await client.http.POST(
                "/api/v1/invitations/{invitation_id}/accept",
                {
                  params: { path: { invitation_id: invitationId } },
                  body: { token, password, ...(name ? { name } : {}) },
                },
              ),
            )
          : data(
              await client.http.POST("/api/v1/auth/login", {
                body: { email, password },
              }),
            );
      auth.authenticated(result.csrf_token);
      navigate(`/${pairingSearch(location.search)}`, { replace: true });
    },
  });
  useEffect(() => {
    if (mutation.isSuccess && window.location.hash)
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
  }, [mutation.isSuccess]);
  const asksEmail = mode === "login" || mode === "forgot";
  const asksPassword = ["login", "invite", "reset"].includes(mode);
  const missingToken = ["reset", "invite", "email"].includes(mode) && !token;
  const withoutDelivery =
    mode === "forgot" && configuration.data?.email_delivery === false;
  const failure = readFailure(mutation.error, mode, t);
  const fieldError =
    failure?.field && asksPassword ? failure.message : undefined;
  const noticeMessage = failure && !fieldError ? failure.message : undefined;
  const succeeded =
    mutation.isSuccess && ["forgot", "reset", "email"].includes(mode);
  const passwordRef = useRef<HTMLInputElement>(null);
  /** A rejected credential returns the reader to the field that owns the fix. */
  useEffect(() => {
    if (fieldError) passwordRef.current?.focus();
  }, [fieldError]);
  const titles: Record<Mode, string> = {
    login: t("Sign in"),
    invite: t("Join your team"),
    forgot: t("Reset your password"),
    reset: t("Choose a new password"),
    email: t("Confirm your email"),
  };
  const descriptions: Record<Mode, string> = {
    login: t("Build, run, and observe your agents."),
    invite: t("Create your account to accept this invitation."),
    forgot: t("We'll send a recovery link if your email is eligible."),
    reset: t("Use at least 15 characters for your new password."),
    email: t("Confirm this address while signed in to your account."),
  };
  const submitLabels: Record<Mode, string> = {
    login: t("Sign in"),
    invite: t("Accept invitation"),
    forgot: t("Send recovery link"),
    reset: t("Reset password"),
    email: t("Confirm email"),
  };
  if (mode === "login" && auth.data && !auth.anonymous)
    return <Navigate to={`/${pairingSearch(location.search)}`} replace />;
  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }
  /** A stale failure must not outlive the correction the reader is typing. */
  function edit(apply: (value: string) => void) {
    return (event: { target: { value: string } }) => {
      if (mutation.isError) mutation.reset();
      apply(event.target.value);
    };
  }
  const links = (
    <div className={styles.links}>
      {mode === "login" && configuration.data?.email_delivery && (
        <Link className={styles.link} to="/forgot-password">
          {t("Forgot password")}
        </Link>
      )}
      {(mode === "forgot" || mode === "reset") && (
        <Link className={styles.link} to="/login">
          <ArrowLeftIcon size={13} aria-hidden="true" />
          {t("Back to sign in")}
        </Link>
      )}
      {mode === "email" && auth.anonymous && (
        <a
          className={styles.link}
          href="/login"
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("Sign in")}
        </a>
      )}
    </div>
  );
  return (
    <main className={styles.screen}>
      <div className={styles.column}>
        <div className={styles.brand}>
          <Logo alt="" width={26} height={26} />
          <Wordmark />
        </div>
        <h1 className={styles.title}>{titles[mode]}</h1>
        <p className={styles.description}>{descriptions[mode]}</p>
        {succeeded ? (
          <div className={styles.success}>
            <CheckCircleIcon size={20} weight="fill" aria-hidden="true" />
            <h2>
              {mode === "forgot" ? t("Check your inbox") : t("You're all set")}
            </h2>
            <p>
              {mode === "forgot"
                ? t(
                    "If the address can receive a recovery link, it will arrive shortly.",
                  )
                : t("Your account has been updated.")}
            </p>
            <Button
              variant="outline"
              render={
                <Link to={mode === "email" ? "/settings/profile" : "/login"} />
              }
            >
              {t("Continue")}
            </Button>
          </div>
        ) : (
          <form onSubmit={submit} className={styles.form}>
            {mode === "invite" && (
              <FormField label={t("Your name")}>
                <Input
                  size="lg"
                  required={true}
                  autoComplete="name"
                  value={name}
                  onChange={edit(setName)}
                  maxLength={128}
                />
              </FormField>
            )}
            {asksEmail && (
              <FormField label={t("Email address")}>
                <Input
                  size="lg"
                  required={true}
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={edit(setEmail)}
                  aria-invalid={fieldError ? true : undefined}
                  aria-describedby={fieldError ? passwordErrorId : undefined}
                />
              </FormField>
            )}
            {asksPassword && (
              <FormField
                label={t("Password")}
                description={
                  mode === "invite" ? t("At least 15 characters.") : undefined
                }
                error={fieldError}
              >
                <Input
                  ref={passwordRef}
                  id={passwordFieldId}
                  size="lg"
                  required={true}
                  type="password"
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  value={password}
                  onChange={edit(setPassword)}
                  minLength={mode === "login" ? 1 : 15}
                  maxLength={128}
                />
              </FormField>
            )}
            {missingToken && (
              <Notice>
                {t(
                  "The link is missing its verification token. Open the complete link from your email.",
                )}
              </Notice>
            )}
            {withoutDelivery && (
              <Notice>
                {t(
                  "Email delivery is not configured. Contact your organization administrator.",
                )}
              </Notice>
            )}
            {mode === "email" && auth.isPending && <Loading />}
            {mode === "email" && auth.anonymous && (
              <Notice
                tone="info"
                action={
                  <Button
                    size="sm"
                    variant="outline"
                    type="button"
                    onClick={() => void auth.refresh()}
                  >
                    {t("Refresh sign-in")}
                  </Button>
                }
              >
                {t(
                  "Sign in in another tab, then return here to confirm your email.",
                )}
              </Notice>
            )}
            {noticeMessage && <Notice>{noticeMessage}</Notice>}
            <Button
              className={styles.submit}
              type="submit"
              size="lg"
              variant="default"
              disabled={
                missingToken ||
                withoutDelivery ||
                (mode === "email" && (!auth.data || auth.anonymous))
              }
              loading={mutation.isPending}
            >
              {submitLabels[mode]}
            </Button>
          </form>
        )}
        {links}
      </div>
      <p className={styles.tagline}>{t("The foundation for your agents.")}</p>
    </main>
  );
}
