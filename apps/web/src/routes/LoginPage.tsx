import { useNavigate, useSearch } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Banner, Button, Card, Input } from "@/components/ui/primitives";
import { useAuthProviders, useLogin, useSignup } from "@/features/auth/useAuth";
import { type ApiError, type User, api } from "@/lib/api";

/**
 * Signup / login.
 *
 * Google is the primary button — one tap, no password to invent — with
 * email+password as the fallback (FRONTEND_DESIGN.md § first run). Account
 * creation is deliberately separate from Strava so signup can never be blocked
 * by Strava's new-app athlete cap.
 */
export function LoginPage() {
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { error?: string };
  const { data: providers } = useAuthProviders();
  const login = useLogin();
  const signup = useSignup();

  const pending = login.isPending || signup.isPending;
  const failure = (login.error ?? signup.error) as ApiError | null;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);

    if (mode === "login") {
      await login.mutateAsync({ email, password }).catch(() => null);
      if (!login.isError) navigate({ to: "/" });
      return;
    }

    const result = await signup.mutateAsync({ email, password }).catch(() => null);
    if (!result) return;

    // Signup responses are deliberately identical whether or not the address is
    // already registered (AUTH.md §5), so the *response* can't tell us whether a
    // session exists. Ask instead: a real new account is signed in and goes
    // straight into the product; an existing one stays here with the neutral
    // message rather than bouncing off a dashboard it can't load.
    const user = await api.get<User>("/auth/me").catch(() => null);
    if (user) {
      navigate({ to: "/" });
      return;
    }
    setNotice(result.message);
    setMode("login");
  }

  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col justify-center gap-6 px-4 py-12">
      <header className="text-center">
        <h1 className="text-2xl font-semibold text-[var(--text-primary)]">
          See ten years of your training, properly
        </h1>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          All the analysis Strava charges for — plus a few things it doesn't have. Your data
          stays yours.
        </p>
      </header>

      {search.error === "account_exists" && (
        <Banner tone="warning" title="You already have an account">
          An account already exists for that email address. Sign in with your password, then
          link Google from Settings.
        </Banner>
      )}
      {search.error && search.error !== "account_exists" && (
        <Banner tone="warning" title="Google sign-in didn't complete">
          Something went wrong on the way back from Google. Please try again.
        </Banner>
      )}

      <Card className="flex flex-col gap-4">
        {providers?.google && (
          <>
            <a
              href="/api/auth/google/start"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-4 py-2.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--page)]"
            >
              <span aria-hidden="true" className="font-bold">
                G
              </span>
              Continue with Google
            </a>
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-[var(--border)]" />
              <span className="text-xs text-[var(--text-muted)]">or</span>
              <span className="h-px flex-1 bg-[var(--border)]" />
            </div>
          </>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input
            name="email"
            type="email"
            label="Email address"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />

          <div className="flex flex-col gap-1.5">
            <Input
              name="password"
              type={showPassword ? "text" : "password"}
              label="Password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              // A live hint rather than a red error before they've even submitted.
              hint={
                mode === "signup"
                  ? "12+ characters — a passphrase works great."
                  : undefined
              }
            />
            {/* A "show password" toggle beats a confirm-password field. */}
            <button
              type="button"
              className="self-start text-xs text-[var(--text-muted)] underline"
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? "Hide password" : "Show password"}
            </button>
          </div>

          {failure && (
            <p role="alert" className="text-sm text-[var(--status-critical)]">
              {failure.message}
            </p>
          )}
          {notice && <p className="text-sm text-[var(--text-secondary)]">{notice}</p>}

          <Button type="submit" loading={pending}>
            {mode === "signup" ? "Create account" : "Log in"}
          </Button>
        </form>

        <p className="text-center text-sm text-[var(--text-secondary)]">
          {mode === "signup" ? "Already have an account?" : "New here?"}{" "}
          <button
            type="button"
            className="font-medium text-[var(--series-1)] underline"
            onClick={() => {
              setMode(mode === "signup" ? "login" : "signup");
              login.reset();
              signup.reset();
            }}
          >
            {mode === "signup" ? "Log in" : "Create an account"}
          </button>
        </p>
      </Card>

      <p className="text-center text-xs text-[var(--text-muted)]">
        We only ever read your Strava data. We never post.
      </p>
    </main>
  );
}
