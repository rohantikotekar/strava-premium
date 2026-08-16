import { ApiError } from "@/lib/api";
import { QueryClient } from "@tanstack/react-query";

/**
 * TanStack Query owns all server state (CLAUDE.md §3). No server data lives in
 * useState or Context.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Keep the previous page visible while refetching, so changing a filter
      // never blanks the dashboard.
      placeholderData: (previous: unknown) => previous,
      retry: (failureCount, error) => {
        // 401 means "signed out" — retrying just delays the redirect.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
    },
  },
});
