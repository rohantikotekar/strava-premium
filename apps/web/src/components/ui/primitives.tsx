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
    "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors " +
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--series-1)] " +
    "disabled:opacity-50 disabled:cursor-not-allowed";
  // 44px min target on touch (FRONTEND_DESIGN.md § accessibility).
  const sizes = { sm: "px-3 py-1.5 text-sm min-h-9", md: "px-4 py-2.5 text-sm min-h-11" };
  const variants = {
    primary: "bg-[var(--series-1)] text-white hover:opacity-90",
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
        "rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-5",
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
        {title && <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>}
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
  tone?: "neutral" | "estimate";
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        tone === "estimate"
          ? "bg-[var(--status-warning)]/15 text-[var(--text-secondary)]"
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
      <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>
      {children && (
        <p className="max-w-md text-sm text-[var(--text-secondary)]">{children}</p>
      )}
      {action}
    </div>
  );
}

// ---- Tooltip / info popover -------------------------------------------------

export function InfoDot({ label, what, why }: { label: string; what: string; why: string }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={`What is ${label}?`}
        className="flex h-4 w-4 items-center justify-center rounded-full border border-[var(--border)] text-[10px] text-[var(--text-muted)]"
      >
        ?
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-64 -translate-x-1/2 rounded-lg border border-[var(--border)] bg-[var(--surface-1)] p-3 text-left text-xs shadow-lg group-hover:block group-focus-within:block"
      >
        <span className="block font-semibold text-[var(--text-primary)]">{label}</span>
        <span className="mt-1 block text-[var(--text-secondary)]">{what}</span>
        <span className="mt-1 block text-[var(--text-muted)]">{why}</span>
      </span>
    </span>
  );
}
