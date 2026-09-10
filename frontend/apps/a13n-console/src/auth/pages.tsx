import { Button, FormField, Input } from "a13n-ui";

import { Logo, Wordmark } from "a13n-ui";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
} from "react-router";

import { ArrowRightIcon, CheckCircleIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { data } from "../shared/api";
import { ErrorNotice, Loading } from "../shared/feedback";
import { useAuth, useClient } from "./context";
import styles from "./pages.module.css";

export function AuthPage() {
  const client = useClient(),
    auth = useAuth(),
    location = useLocation(),
    navigate = useNavigate();
  const { invitationId } = useParams();
  const { t } = useTranslation();
  const mode = invitationId
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
      navigate("/", { replace: true });
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
  const titles = {
    login: t("Welcome back"),
    invite: t("Join your team"),
    forgot: t("Reset your password"),
    reset: t("Choose a new password"),
    email: t("Confirm your email"),
  };
  const descriptions = {
    login: t("Sign in to build, run, and observe your agents."),
    invite: t("Create your account to accept this invitation."),
    forgot: t("We'll send a recovery link if your email is eligible."),
    reset: t("Use at least 15 characters for your new password."),
    email: t("Confirm this address while signed in to your account."),
  };
  if (mode === "login" && auth.data && !auth.anonymous)
    return <Navigate to="/" replace />;
  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }
  return (
    <main className={styles.screen}>
      <div className={styles.brand}>
        <Logo alt="" />
        <Wordmark />
      </div>
      <section className={styles.card}>
        <h1>{titles[mode]}</h1>
        <p>{descriptions[mode]}</p>
        {mutation.isSuccess && ["forgot", "reset", "email"].includes(mode) ? (
          <div className={styles.success}>
            <CheckCircleIcon size={30} />
            <h2>
              {t(mode === "forgot" ? "Check your inbox" : "You're all set")}
            </h2>
            <p>
              {t(
                mode === "forgot"
                  ? "If the address can receive a recovery link, it will arrive shortly."
                  : "Your account has been updated.",
              )}
            </p>
            <Link to={mode === "email" ? "/settings/profile" : "/login"}>
              {t("Continue")}
            </Link>
          </div>
        ) : (
          <form onSubmit={submit} className={styles.form}>
            {mode === "invite" && (
              <FormField className="min-w-0 w-full" label={t("Your name")}>
                <Input
                  required={true}
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={128}
                />
              </FormField>
            )}
            {["login", "forgot"].includes(mode) && (
              <FormField className="min-w-0 w-full" label={t("Email address")}>
                <Input
                  required={true}
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </FormField>
            )}
            {["login", "invite", "reset"].includes(mode) && (
              <FormField className="min-w-0 w-full" label={t("Password")}>
                <Input
                  required={true}
                  type="password"
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={mode === "login" ? 1 : 15}
                  maxLength={128}
                />
              </FormField>
            )}
            {["reset", "invite", "email"].includes(mode) && !token && (
              <ErrorNotice
                error={
                  new Error(
                    t(
                      "The link is missing its verification token. Open the complete link from your email.",
                    ),
                  )
                }
              />
            )}
            {mode === "forgot" &&
              configuration.data?.email_delivery === false && (
                <ErrorNotice
                  error={
                    new Error(
                      t(
                        "Email delivery is not configured. Contact your organization administrator.",
                      ),
                    )
                  }
                />
              )}
            {mode === "email" && auth.isPending && <Loading />}
            {mode === "email" && auth.anonymous && (
              <p>
                {t(
                  "Sign in in another tab, then return here to confirm your email.",
                )}{" "}
                <a href="/login" target="_blank" rel="noopener noreferrer">
                  {t("Sign in")}
                </a>{" "}
                <Button
                  variant="outline"
                  onClick={() => void auth.refresh()}
                  type="button"
                >
                  {t("Refresh sign-in")}
                </Button>
              </p>
            )}
            <ErrorNotice error={mutation.error} />
            <Button
              type="submit"
              variant="default"
              disabled={
                (["reset", "invite", "email"].includes(mode) && !token) ||
                (mode === "email" && (!auth.data || auth.anonymous)) ||
                (mode === "forgot" &&
                  configuration.data?.email_delivery === false)
              }
              loading={mutation.isPending}
            >
              <ArrowRightIcon size={16} />
              {t(
                mode === "login"
                  ? "Sign in"
                  : mode === "invite"
                    ? "Accept invitation"
                    : mode === "forgot"
                      ? "Send recovery link"
                      : mode === "reset"
                        ? "Reset password"
                        : "Confirm email",
              )}
            </Button>
          </form>
        )}
        {mode === "login" && configuration.data?.email_delivery && (
          <Link className={styles.secondary} to="/forgot-password">
            {t("Forgot your password?")}
          </Link>
        )}
        {mode === "forgot" && (
          <Link className={styles.secondary} to="/login">
            {t("Back to sign in")}
          </Link>
        )}
      </section>
      <footer>{t("The foundation for your agents.")}</footer>
    </main>
  );
}
