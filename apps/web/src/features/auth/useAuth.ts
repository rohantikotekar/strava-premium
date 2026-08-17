import { ApiError, type AuthProviders, type User, api, setSessionToken } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

/**
 * The session query.
 *
 * A 401 is not an error state — it's "signed out", which is a normal condition.
 * Retrying it would just burn requests and delay the redirect to /login.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await api.get<User>("/auth/me");
      } catch (error) {
        // Only a real 401 means the session is gone — drop any stored bearer
        // token so it stops riding along on every later request. A network
        // failure must not wipe a token that is still perfectly valid.
        if (error instanceof ApiError && error.isUnauthorized) setSessionToken(null);
        return null;
      }
    },
    retry: false,
    staleTime: 60_000,
  });
}

export function useAuthProviders() {
  return useQuery({
    queryKey: ["auth", "providers"],
    queryFn: () => api.get<AuthProviders>("/auth/providers"),
    staleTime: Number.POSITIVE_INFINITY, // server config; doesn't change at runtime
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { email: string; password: string }) =>
      api.post<User>("/auth/login", payload),
    onSuccess: (user) => {
      queryClient.setQueryData(["auth", "me"], user);
      queryClient.invalidateQueries();
    },
  });
}

export function useSignup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { email: string; password: string; first_name?: string }) =>
      api.post<{ message: string }>("/auth/signup", payload),
    onSuccess: () => {
      // Signup issues a session, so refetch identity rather than trusting the
      // deliberately-generic message body.
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ message: string }>("/auth/logout"),
    onSuccess: () => {
      // The server revokes the session row; this drops our copy of the token so
      // a signed-out tab can't keep presenting it.
      setSessionToken(null);
      queryClient.setQueryData(["auth", "me"], null);
      queryClient.clear();
    },
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Partial<User>) => api.patch<User>("/me/profile", payload),
    onSuccess: (user) => {
      queryClient.setQueryData(["auth", "me"], user);
      // FTP / HR changes re-derive training load for the whole history.
      queryClient.invalidateQueries({ queryKey: ["chart"] });
    },
  });
}
