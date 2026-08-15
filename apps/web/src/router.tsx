import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useRouterState,
} from "@tanstack/react-router";
import { Button, Spinner } from "@/components/ui/primitives";
import { useCurrentUser, useLogout } from "@/features/auth/useAuth";
import { type User, api } from "@/lib/api";
import { queryClient } from "@/queryClient";
import { ActivitiesPage, ActivityDetailPage } from "@/routes/ActivitiesPage";
import { DashboardPage } from "@/routes/DashboardPage";
import { ImportPage } from "@/routes/ImportPage";
import { LoginPage } from "@/routes/LoginPage";
import { ProgressPage } from "@/routes/ProgressPage";
import { SettingsPage } from "@/routes/SettingsPage";

/** Cached identity check shared by every route guard. */
async function fetchUser(): Promise<User | null> {
  return queryClient.fetchQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await api.get<User>("/auth/me");
      } catch {
        return null;
      }
    },
    staleTime: 30_000,
  });
}

/**
 * Guard for authenticated routes.
 *
 * Routes are kept flat rather than nested under a pathless layout route — the
 * shell is applied by RootLayout instead. That keeps `to` and `from` referring to
 * real URL paths, which is what makes the router's link types checkable.
 */
async function requireAuth() {
  const user = await fetchUser();
  if (!user) throw redirect({ to: "/login", search: {} });
  return { user };
}

const rootRoute = createRootRoute({ component: RootLayout });

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: (search: Record<string, unknown>): { error?: string } =>
    typeof search.error === "string" ? { error: search.error } : {},
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: requireAuth,
  component: DashboardPage,
});
const activitiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/activities",
  beforeLoad: requireAuth,
  component: ActivitiesPage,
});
const activityDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/activities/$activityId",
  beforeLoad: requireAuth,
  component: ActivityDetailPage,
});
const progressRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/progress",
  beforeLoad: requireAuth,
  component: ProgressPage,
});
const importRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/import",
  beforeLoad: requireAuth,
  component: ImportPage,
});
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  beforeLoad: requireAuth,
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  indexRoute,
  activitiesRoute,
  activityDetailRoute,
  progressRoute,
  importRoute,
  settingsRoute,
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

/** Five nav items. Not eleven (FRONTEND_DESIGN.md § information architecture). */
const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/activities", label: "Activities" },
  { to: "/progress", label: "Progress" },
  { to: "/import", label: "Import" },
  { to: "/settings", label: "Settings" },
] as const;

function RootLayout() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  // The login page is full-bleed; everything else gets the app shell.
  if (pathname.startsWith("/login")) return <Outlet />;
  return <AppShell />;
}

function AppShell() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();
  const isLoading = useRouterState({ select: (state) => state.isLoading });

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 border-b border-[var(--border)] bg-[var(--surface-1)]">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <span className="shrink-0 font-semibold text-[var(--text-primary)]">
            Strava Premium
          </span>

          <nav className="scroll-x flex flex-1 gap-1" aria-label="Main">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="shrink-0 rounded-lg px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--page)]"
                activeProps={{
                  className:
                    "shrink-0 rounded-lg px-3 py-2 text-sm font-medium bg-[var(--page)] text-[var(--text-primary)]",
                }}
                activeOptions={{ exact: item.to === "/" }}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="flex shrink-0 items-center gap-2">
            {isLoading && <Spinner className="text-[var(--text-muted)]" />}
            <span className="hidden text-xs text-[var(--text-muted)] sm:inline">
              {user?.email}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                logout.mutate(undefined, {
                  onSuccess: () => router.navigate({ to: "/login", search: {} }),
                })
              }
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-[var(--border)] px-4 py-4 text-center text-xs text-[var(--text-muted)]">
        Not affiliated with Strava. Powered by Strava.
      </footer>
    </div>
  );
}
