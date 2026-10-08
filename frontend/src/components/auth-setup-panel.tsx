"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiRequestError } from "@/lib/api";

export function AuthSetupPanel({ error }: { error: ApiRequestError }) {
  const missingConfiguration = error.status === 503;

  return (
    <main className="grid min-h-dvh place-items-center px-4 py-10">
      <section className="w-full max-w-2xl rounded-[7px] border border-[var(--warning)]/35 bg-[var(--surface)] p-5 shadow-sm sm:p-7">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-[5px] bg-[var(--warning-soft)] text-[var(--warning)]">
            {missingConfiguration ? <AlertTriangle size={18} /> : <ShieldCheck size={18} />}
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase text-[var(--warning)]">Local API identity</p>
            <h1 className="mt-1 text-xl font-bold tracking-tight">
              {missingConfiguration ? "Authentication configuration is incomplete" : "The configured identity was rejected"}
            </h1>
            <p className="mt-2 break-words text-sm text-[var(--muted)]">{error.message}</p>
          </div>
        </div>

        <div className="mt-5 border-t border-[var(--border)] pt-4">
          <h2 className="text-sm font-semibold">Configure the local development identity</h2>
          <ol className="mt-3 grid gap-3 text-sm text-[var(--muted)]">
            <li><span className="mr-2 font-mono text-[var(--accent)]">01</span>From <code>frontend/</code>, copy <code>.env.example</code> to <code>.env.local</code>.</li>
            <li><span className="mr-2 font-mono text-[var(--accent)]">02</span>Set <code>AUTH_PROXY_SECRET</code> in <code>.env.local</code> and the FastAPI process to the same private value. Do not prefix it with <code>NEXT_PUBLIC_</code>.</li>
            <li><span className="mr-2 font-mono text-[var(--accent)]">03</span>Provision a database user, then set <code>REVIEW_USER_ID</code> to the printed ID.</li>
            <li><span className="mr-2 font-mono text-[var(--accent)]">04</span>Restart Next.js and FastAPI. For a rejected identity, verify the user ID and secret match.</li>
          </ol>
          <pre className="mono mt-4 overflow-x-auto rounded-[5px] border border-[var(--border)] bg-[var(--code-bg)] p-3 text-xs leading-6 text-[var(--foreground)]">{`# Backend shell
export AUTH_PROXY_SECRET="<same-private-secret>"
export REPOSITORY_ROOTS="/path/to/allowed/repos"
python -m app.db.provision_user reviewer reviewer@example.com REVIEWER

# frontend/.env.local
FASTAPI_BASE_URL=http://127.0.0.1:8000
REVIEW_USER_ID=<printed-user-id>
AUTH_PROXY_SECRET=<same-private-secret>`}</pre>
          <p className="mt-3 text-xs text-[var(--muted)]">
            The browser talks only to the same-origin Next.js proxy. The proxy secret stays server-side; never send it from client code or commit `.env.local`.
          </p>
          <div className="mt-5 flex justify-end">
            <Button variant="secondary" onClick={() => window.location.reload()}>Retry connection</Button>
          </div>
        </div>
      </section>
    </main>
  );
}