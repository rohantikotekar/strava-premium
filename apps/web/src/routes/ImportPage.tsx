import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Banner, Button, Card, EmptyState } from "@/components/ui/primitives";
import { type ImportStatus, type UploadCreated, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

/**
 * The import wizard.
 *
 * Bytes go browser -> object store via a presigned URL; the API only ever mints
 * the URL and receives the "done" call (CLAUDE.md §8). Progress is pushed over
 * SSE, and the key moment is `dashboard_ready` — the user gets a working
 * dashboard long before the deep parse finishes.
 */
export function ImportPage() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadPct, setUploadPct] = useState(0);
  const [status, setStatus] = useState<ImportStatus | null>(null);
  const [progress, setProgress] = useState<{
    items_done: number;
    items_total: number;
    items_failed: number;
    activities_found: number;
    dashboard_ready: boolean;
    status: string;
  } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const { data: history } = useQuery({
    queryKey: ["imports"],
    queryFn: () => api.get<ImportStatus[]>("/imports"),
  });

  // Subscribe to the server-sent progress stream once an import is running.
  useEffect(() => {
    if (!status || phase !== "processing") return;

    const source = new EventSource(`/api/imports/${status.id}/events`, {
      withCredentials: true,
    });

    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      setProgress(payload);
      if (payload.status === "complete" || payload.status === "failed") {
        setPhase(payload.status === "complete" ? "done" : "error");
        if (payload.error) setMessage(payload.error);
        queryClient.invalidateQueries();
        source.close();
      }
    };
    source.onerror = () => source.close();

    return () => source.close();
  }, [status, phase, queryClient]);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      setPhase("uploading");
      setUploadPct(0);
      setMessage(null);

      const created = await api.post<UploadCreated>("/uploads", {
        filename: file.name,
        size_bytes: file.size,
      });

      // XHR rather than fetch: we need real upload progress for a multi-GB file.
      await new Promise<void>((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("PUT", created.upload_url);
        request.setRequestHeader("Content-Type", "application/zip");
        request.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            setUploadPct(Math.round((event.loaded / event.total) * 100));
          }
        };
        request.onload = () =>
          request.status >= 200 && request.status < 300
            ? resolve()
            : reject(new Error(`Upload failed (${request.status})`));
        request.onerror = () => reject(new Error("Upload failed. Check your connection."));
        request.send(file);
      });

      return api.post<ImportStatus>(`/uploads/${created.upload_id}/complete`);
    },
    onSuccess: (result) => {
      setStatus(result);
      setPhase("processing");
    },
    onError: (error: Error) => {
      setPhase("error");
      setMessage(error.message);
    },
  });

  function handleFile(file: File | undefined) {
    if (!file) return;
    // Catch the wrong file *before* a multi-GB upload.
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setPhase("error");
      setMessage(
        "That isn't a .zip. Upload the archive Strava emailed you — not an individual file.",
      );
      return;
    }
    upload.mutate(file);
  }

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-xl font-semibold text-[var(--text-primary)]">Import your history</h1>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          Ten years of data, analysed locally. Nothing leaves your machine.
        </p>
      </header>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Step 1 — Request your archive from Strava
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Strava has to prepare your file. It usually takes a few hours, and they'll email
            you a download link.
          </p>
          <a
            href="https://www.strava.com/athlete/delete_your_account"
            target="_blank"
            rel="noreferrer noopener"
            className="mt-2 inline-block text-sm font-medium text-[var(--series-1)] underline"
          >
            Open Strava's download page ↗
          </a>
        </div>

        <div className="border-t border-[var(--border)] pt-4">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            Step 2 — Upload it here
          </h2>

          {phase === "idle" && (
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                handleFile(e.dataTransfer.files[0]);
              }}
              className="mt-3 flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-[var(--border)] p-8 text-center"
            >
              <p className="text-sm text-[var(--text-secondary)]">Drop your export.zip here</p>
              <input
                ref={fileInput}
                type="file"
                accept=".zip,application/zip"
                className="hidden"
                onChange={(e) => handleFile(e.target.files?.[0])}
              />
              <Button variant="secondary" onClick={() => fileInput.current?.click()}>
                Choose a file
              </Button>
              <p className="text-xs text-[var(--text-muted)]">
                Usually 500 MB – 10 GB. You can close this tab once the upload finishes.
              </p>
            </div>
          )}

          {phase === "uploading" && (
            <div className="mt-3">
              <p className="text-sm text-[var(--text-secondary)]">Uploading your archive…</p>
              <ProgressBar pct={uploadPct} />
              <p className="mt-1 text-xs text-[var(--text-muted)]">{uploadPct}%</p>
            </div>
          )}

          {(phase === "processing" || phase === "done") && progress && (
            <div className="mt-3 flex flex-col gap-3">
              {progress.dashboard_ready ? (
                <Banner
                  tone="success"
                  title="Your dashboard is ready"
                  action={
                    <a href="/">
                      <Button size="sm">View it now</Button>
                    </a>
                  }
                >
                  We're still analysing detailed data from your files — charts will get richer
                  as it finishes.
                </Banner>
              ) : (
                <p className="text-sm text-[var(--text-secondary)]">
                  Reading your archive — this takes a few seconds…
                </p>
              )}

              {progress.items_total > 0 && (
                <div>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Analysing detailed files — {progress.items_done.toLocaleString()} of{" "}
                    {progress.items_total.toLocaleString()}
                  </p>
                  <ProgressBar
                    pct={Math.round((progress.items_done / progress.items_total) * 100)}
                  />
                </div>
              )}

              {phase === "done" && (
                <Banner tone="success" title="All done">
                  {progress.activities_found.toLocaleString()} activities imported
                  {progress.items_failed > 0 && (
                    <>
                      {" · "}
                      {progress.items_failed.toLocaleString()} files couldn't be read
                    </>
                  )}
                  .
                </Banner>
              )}
            </div>
          )}

          {phase === "error" && (
            <Banner tone="critical" title="That didn't work">
              {message ?? "Something went wrong."}
              <Button
                size="sm"
                variant="secondary"
                className="mt-3"
                onClick={() => {
                  setPhase("idle");
                  setMessage(null);
                }}
              >
                Try again
              </Button>
            </Banner>
          )}
        </div>
      </Card>

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Import history</h2>
        {!history || history.length === 0 ? (
          <EmptyState title="No imports yet." />
        ) : (
          <ul className="flex flex-col gap-2">
            {history.map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2 text-sm last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-[var(--text-primary)]">
                    {item.filename ?? "export.zip"}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {formatDateTime(item.created_at)}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-[var(--text-secondary)]">{item.status}</p>
                  {item.items_failed > 0 && (
                    <p className="text-xs text-[var(--text-muted)]">
                      {item.items_failed} unreadable
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div
      className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[var(--page)]"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full bg-[var(--series-1)] transition-[width]"
        style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }}
      />
    </div>
  );
}
