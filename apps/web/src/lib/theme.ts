/**
 * Theme preference: explicit light/dark pin, or "system" (the default —
 * follows `prefers-color-scheme`, no `data-theme` attribute set at all).
 *
 * The CSS side of this lives in index.css: `:root` is the light palette,
 * `@media (prefers-color-scheme: dark)` covers "system says dark, no
 * explicit override", and `:root[data-theme="dark"|"light"]` pins regardless
 * of OS. This module only ever needs to set or clear that one attribute.
 *
 * Applied before paint by an inline script in index.html — that's what
 * avoids a flash of the wrong theme on load; this module is what changes it
 * afterward and keeps the choice for next visit.
 */

import { useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "theme-preference";

export function getStoredTheme(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: ThemePreference): void {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
  try {
    if (theme === "system") {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, theme);
    }
  } catch {
    // Private browsing / storage disabled — the in-memory attribute still
    // works for this session, it just won't persist across visits.
  }
}

/** Whichever mode is actually rendering right now, resolving "system". */
function resolveEffective(theme: ThemePreference): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme(): {
  preference: ThemePreference;
  effective: "light" | "dark";
  setPreference: (theme: ThemePreference) => void;
} {
  const [preference, setPreferenceState] = useState<ThemePreference>(getStoredTheme);
  const [effective, setEffective] = useState<"light" | "dark">(() =>
    resolveEffective(getStoredTheme()),
  );

  useEffect(() => {
    setEffective(resolveEffective(preference));

    if (preference !== "system") return;
    // Only "system" needs to keep listening — an explicit pin doesn't care
    // when the OS setting changes.
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setEffective(resolveEffective("system"));
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference]);

  function setPreference(theme: ThemePreference) {
    applyTheme(theme);
    setPreferenceState(theme);
  }

  return { preference, effective, setPreference };
}
