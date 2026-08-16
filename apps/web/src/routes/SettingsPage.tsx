import { Banner, Button, Card, Input } from "@/components/ui/primitives";
import { useCurrentUser, useUpdateProfile } from "@/features/auth/useAuth";
import { api } from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

/**
 * Settings — the screen where trust is won or lost.
 *
 * Changing FTP or max HR re-derives training load across the whole history, so we
 * say so before doing it rather than silently shifting every chart.
 */
export function SettingsPage() {
  const { data: user } = useCurrentUser();
  const updateProfile = useUpdateProfile();
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const disconnectStrava = useMutation({
    mutationFn: () => api.delete<{ message: string }>("/me/strava"),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  if (!user) return null;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const toNumber = (key: string) => {
      const raw = form.get(key);
      const value = raw === null || raw === "" ? null : Number(raw);
      return Number.isFinite(value) ? value : null;
    };

    updateProfile.mutate(
      {
        first_name: (form.get("first_name") as string) || null,
        weight_kg: toNumber("weight_kg"),
        ftp_w: toNumber("ftp_w"),
        max_hr_bpm: toNumber("max_hr_bpm"),
        resting_hr_bpm: toNumber("resting_hr_bpm"),
        measurement_pref: form.get("measurement_pref") as "metric" | "imperial",
      },
      { onSuccess: () => setSaved(true) },
    );
  }

  const missingZones = !user.max_hr_bpm && !user.ftp_w;

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-3xl font-bold tracking-tight text-[var(--text-primary)]">Settings</h1>

      {missingZones && (
        <Banner tone="warning" title="Your training load is estimated">
          Without a max heart rate or FTP we estimate load from duration alone. Set either one below
          and we'll recompute your whole history properly.
        </Banner>
      )}

      <Card>
        <h2 className="mb-1 text-base font-semibold text-[var(--text-primary)]">
          Zones & thresholds
        </h2>
        <p className="mb-4 text-xs text-[var(--text-secondary)]">
          Changing these recomputes training load for every activity you have. It takes about a
          minute.
        </p>

        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input name="first_name" label="First name" defaultValue={user.first_name ?? ""} />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="measurement_pref" className="text-sm font-medium">
              Units
            </label>
            <select
              id="measurement_pref"
              name="measurement_pref"
              defaultValue={user.measurement_pref}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface-1)] px-3 py-2.5 text-sm"
            >
              <option value="metric">Metric (km, m)</option>
              <option value="imperial">Imperial (mi, ft)</option>
            </select>
          </div>

          <Input
            name="max_hr_bpm"
            type="number"
            label="Max heart rate (bpm)"
            defaultValue={user.max_hr_bpm ?? ""}
            hint="Highest you've actually seen, not 220 minus your age."
            min={100}
            max={260}
          />
          <Input
            name="resting_hr_bpm"
            type="number"
            label="Resting heart rate (bpm)"
            defaultValue={user.resting_hr_bpm ?? ""}
            min={20}
            max={120}
          />
          <Input
            name="ftp_w"
            type="number"
            label="FTP (watts)"
            defaultValue={user.ftp_w ?? ""}
            hint="Cycling only — roughly what you could hold for an hour."
            min={1}
            max={800}
          />
          <Input
            name="weight_kg"
            type="number"
            step="0.1"
            label="Weight (kg)"
            defaultValue={user.weight_kg ?? ""}
            min={20}
            max={300}
          />

          <div className="sm:col-span-2">
            <Button type="submit" loading={updateProfile.isPending}>
              Save and recompute
            </Button>
            {saved && !updateProfile.isPending && (
              <span className="ml-3 text-sm text-[var(--delta-good)]">
                Saved. Recomputing your history…
              </span>
            )}
          </div>
        </form>
      </Card>

      <Card>
        <h2 className="mb-1 text-base font-semibold text-[var(--text-primary)]">Account</h2>
        <dl className="mt-3 flex flex-col gap-2 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-[var(--text-secondary)]">Email</dt>
            <dd className="text-[var(--text-primary)]">{user.email}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-[var(--text-secondary)]">Sign-in methods</dt>
            <dd className="text-[var(--text-primary)]">
              {[user.has_password && "Password", user.has_google && "Google"]
                .filter(Boolean)
                .join(", ") || "—"}
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-[var(--text-secondary)]">Strava</dt>
            <dd className="text-[var(--text-primary)]">
              {user.strava_connected ? "Connected" : "Not connected"}
            </dd>
          </div>
        </dl>

        {user.strava_connected && (
          <div className="mt-4 border-t border-[var(--border)] pt-4">
            <Button
              variant="secondary"
              size="sm"
              loading={disconnectStrava.isPending}
              onClick={() => disconnectStrava.mutate()}
            >
              Disconnect Strava
            </Button>
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              Your imported history stays, and your account keeps working. Only the live sync stops.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
