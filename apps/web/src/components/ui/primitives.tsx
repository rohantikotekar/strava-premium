import { useTheme } from "@/lib/theme";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

// ---- Button ----------------------------------------------------------------

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "primary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition-all " +
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] " +
    "disabled:opacity-50 disabled:cursor-not-allowed";
  // 44px min target on touch (FRONTEND_DESIGN.md § accessibility).
  const sizes = { sm: "px-3 py-1.5 text-sm min-h-9", md: "px-4 py-2.5 text-[0.95rem] min-h-11" };
  const variants = {
    // The one splash of brand color (FRONTEND_DESIGN.md's accent, not a chart
    // series) — every primary call-to-action in the app uses it.
    primary:
      "bg-[var(--accent)] text-[var(--accent-ink)] shadow-sm hover:shadow-md hover:brightness-105 active:brightness-95",
    secondary:
      "border border-[var(--border)] bg-[var(--surface-1)] text-[var(--text-primary)] hover:bg-[var(--page)]",
    ghost: "text-[var(--text-secondary)] hover:bg-[var(--page)]",
    danger: "bg-[var(--status-critical)] text-white hover:opacity-90",
  };

  return (
    <button
      className={cx(base, sizes[size], variants[variant], className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

// ---- Input -----------------------------------------------------------------

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Input({ label, hint, error, className, id, ...rest }: InputProps) {
  const inputId = id ?? rest.name;
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium text-[var(--text-primary)]">
          {label}
        </label>
      )}
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
        className={cx(
          "w-full rounded-lg border bg-[var(--surface-1)] px-3 py-2.5 text-sm text-[var(--text-primary)]",
          "placeholder:text-[var(--text-muted)]",
          "focus:outline-2 focus:outline-offset-0 focus:outline-[var(--series-1)]",
          error ? "border-[var(--status-critical)]" : "border-[var(--border)]",
          className,
        )}
        {...rest}
      />
      {/* Hints stay visible; errors replace them only once there is one. */}
      {error ? (
        <p id={`${inputId}-error`} className="text-xs text-[var(--status-critical)]">
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="text-xs text-[var(--text-muted)]">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

// ---- Card ------------------------------------------------------------------

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={cx(
        "card-elevated rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-6",
        className,
      )}
    >
      {children}
    </section>
  );
}

// ---- Banner ----------------------------------------------------------------

export function Banner({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "warning" | "success" | "critical";
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  // Status colour always ships with an icon + label, never colour alone.
  const tones = {
    info: { border: "var(--series-1)", icon: "i" },
    warning: { border: "var(--status-warning)", icon: "!" },
    success: { border: "var(--status-good)", icon: "✓" },
    critical: { border: "var(--status-critical)", icon: "!" },
  };
  const { border, icon } = tones[tone];

  return (
    <div
      className="flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--surface-1)] p-4"
      style={{ borderLeftWidth: 3, borderLeftColor: border }}
      role={tone === "critical" ? "alert" : "status"}
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
        style={{ background: border }}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        {title && <p className="text-base font-semibold text-[var(--text-primary)]">{title}</p>}
        <div className="text-sm text-[var(--text-secondary)]">{children}</div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

// ---- Badge -----------------------------------------------------------------

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "estimate" | "accent";
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-sm font-medium",
        tone === "estimate"
          ? "bg-[var(--status-warning)]/15 text-[var(--text-secondary)]"
          : tone === "accent"
            ? "bg-[var(--accent-wash)] text-[var(--accent)]"
            : "bg-[var(--page)] text-[var(--text-secondary)]",
      )}
    >
      {tone === "estimate" && <span aria-hidden="true">≈</span>}
      {children}
    </span>
  );
}

// ---- Skeleton --------------------------------------------------------------

/** Chart-shaped, not a spinner — the page shouldn't jump when data lands. */
export function ChartSkeleton({ height = 240 }: { height?: number }) {
  return (
    <div
      className="animate-pulse rounded-lg bg-[var(--page)]"
      style={{ height }}
      aria-hidden="true"
    />
  );
}

// ---- Empty state -----------------------------------------------------------

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <p className="text-base font-semibold text-[var(--text-primary)]">{title}</p>
      {children && <p className="max-w-md text-sm text-[var(--text-secondary)]">{children}</p>}
      {action}
    </div>
  );
}

// ---- Tooltip / info popover -------------------------------------------------

interface InfoDotEntry {
  label: string;
  what: string;
  why: string;
}

/**
 * One "?" affordance, even when it explains several related terms (e.g. a
 * chart titled "Fitness & freshness" covers CTL, ATL and TSB at once) —
 * three separate icons next to a title reads as clutter, not helpfulness.
 */
export function InfoDot(props: InfoDotEntry | { entries: InfoDotEntry[] }) {
  const entries: InfoDotEntry[] = "entries" in props ? props.entries : [props];
  const ariaLabel =
    entries.length === 1 ? `What is ${entries[0]?.label}?` : "What do these terms mean?";

  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        className="flex h-[18px] w-[18px] items-center justify-center rounded-full border border-[var(--border)] bg-[var(--page)] text-[10px] font-semibold text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-wash)] hover:text-[var(--accent)]"
      >
        ?
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-64 -translate-x-1/2 flex-col gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--surface-1)] p-3 text-left text-xs shadow-lg group-hover:flex group-focus-within:flex"
      >
        {entries.map((entry) => (
          <span key={entry.label}>
            <span className="block font-semibold text-[var(--text-primary)]">{entry.label}</span>
            <span className="mt-1 block text-[var(--text-secondary)]">{entry.what}</span>
            <span className="mt-1 block text-[var(--text-muted)]">{entry.why}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

// ---- Theme toggle ------------------------------------------------------------

/**
 * Light/dark switch. Cycles light -> dark -> light — an explicit choice, once
 * made, always wins over the OS setting (see lib/theme.ts). No icon library:
 * two inline SVGs, since this is the only icon in the app so far.
 */
export function ThemeToggle() {
  const { effective, setPreference } = useTheme();
  const next = effective === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => setPreference(next)}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--text-secondary)] transition-colors hover:bg-[var(--page)] hover:text-[var(--text-primary)]"
    >
      {effective === "dark" ? (
        // Sun — shown in dark mode as the affordance to go light.
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none">
          <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="1.75" />
          <path
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            d="M12 2.5v2.25M12 19.25v2.25M4.22 4.22l1.59 1.59M18.19 18.19l1.59 1.59M2.5 12h2.25M19.25 12h2.25M4.22 19.78l1.59-1.59M18.19 5.81l1.59-1.59"
          />
        </svg>
      ) : (
        // Moon — shown in light mode as the affordance to go dark.
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none">
          <path
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinejoin="round"
            d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z"
          />
        </svg>
      )}
    </button>
  );
}
