"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleDot,
  Clock3,
  FilePlus2,
  GitCommitHorizontal,
  Search,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiRequestError, createSession, getCurrentUser, listSessions } from "@/lib/api";
import type { CreateSessionInput, ReviewSessionSummary, ReviewStatus } from "@/lib/types";
import { AuthSetupPanel } from "@/components/auth-setup-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type SortKey = "id" | "source_commit" | "status" | "creator" | "updated_at";
type SortState = { key: SortKey; direction: "asc" | "desc" };

const statusClasses: Record<ReviewStatus, string> = {
  AWAITING_REVIEW: "border-[var(--warning)]/40 bg-[var(--warning-soft)] text-[var(--warning)]",
  APPROVED: "border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[var(--accent)]",
  REJECTED: "border-[var(--danger)]/40 bg-[var(--danger-soft)] text-[var(--danger)]",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function StatusBadge({ status }: { status: ReviewStatus }) {
  return (
    <Badge className={statusClasses[status]}>
      <span className="size-1.5 rounded-full bg-current" />
      {status.replaceAll("_", " ").toLowerCase()}
    </Badge>
  );
}

function CreateSessionDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<CreateSessionInput>({
    source_repo: "",
    docs_repo: "",
    base_revision: "",
    head_revision: "HEAD",
    model_name: "",
  });
  const queryClient = useQueryClient();
  const router = useRouter();
  const mutation = useMutation({
    mutationFn: createSession,
    onSuccess: async (session) => {
      toast.success("Review session created");
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setOpen(false);
      router.push(`/sessions/${session.id}`);
    },
    onError: (error) => toast.error("Could not create session", { description: error.message }),
  });

  function update<K extends keyof CreateSessionInput>(key: K, value: CreateSessionInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate(form);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button><FilePlus2 size={15} />Create New Session</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New documentation review</DialogTitle>
        <DialogDescription>Analyze a product repository revision range and prepare a reviewable patch.</DialogDescription>
        <form onSubmit={submit} className="mt-5 grid gap-4">
          <label className="grid gap-1.5 text-xs font-semibold text-[var(--muted)]">
            Product repository path
            <Input required value={form.source_repo} onChange={(event) => update("source_repo", event.target.value)} placeholder="/srv/product-repositories/service" />
          </label>
          <label className="grid gap-1.5 text-xs font-semibold text-[var(--muted)]">
            Documentation repository path
            <Input required value={form.docs_repo} onChange={(event) => update("docs_repo", event.target.value)} placeholder="/srv/documentation-repositories/product-docs" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1.5 text-xs font-semibold text-[var(--muted)]">
              Base commit / ref
              <Input required className="mono" value={form.base_revision} onChange={(event) => update("base_revision", event.target.value)} placeholder="release-4.1" />
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-[var(--muted)]">
              Head commit / ref
              <Input required className="mono" value={form.head_revision} onChange={(event) => update("head_revision", event.target.value)} placeholder="HEAD" />
            </label>
          </div>
          <label className="grid gap-1.5 text-xs font-semibold text-[var(--muted)]">
            Local Ollama model
            <Input required value={form.model_name} onChange={(event) => update("model_name", event.target.value)} placeholder="qwen3-coder:30b" />
          </label>
          <div className="mt-1 flex justify-end gap-2 border-t border-[var(--border)] pt-4">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Analyzing…" : "Analyze changes"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SessionsDashboard() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"ALL" | ReviewStatus>("ALL");
  const [sort, setSort] = useState<SortState>({ key: "updated_at", direction: "desc" });
  const sessionsQuery = useQuery({
    queryKey: ["sessions", status, search],
    queryFn: () => listSessions({ status: status === "ALL" ? undefined : status, search }),
  });
  const userQuery = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser });
  const sessions = sessionsQuery.data ?? [];
  const sortedSessions = [...sessions].sort((a, b) => {
    const values: Record<SortKey, (item: ReviewSessionSummary) => string> = {
      id: (item) => item.id,
      source_commit: (item) => item.source_commit,
      status: (item) => item.status,
      creator: (item) => item.creator.username,
      updated_at: (item) => item.updated_at,
    };
    const comparison = values[sort.key](a).localeCompare(values[sort.key](b));
    return sort.direction === "asc" ? comparison : -comparison;
  });

  function toggleSort(key: SortKey) {
    setSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  }

  function sortHeading(label: string, field: SortKey) {
    const Icon = sort.key !== field ? ArrowUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown;
    return (
      <button type="button" onClick={() => toggleSort(field)} className="inline-flex items-center gap-1.5 hover:text-[var(--foreground)]">
        {label}<Icon size={12} />
      </button>
    );
  }

  const awaiting = sessions.filter((session) => session.status === "AWAITING_REVIEW").length;
  const complete = sessions.filter((session) => session.status !== "AWAITING_REVIEW").length;
  const authenticationError = [sessionsQuery.error, userQuery.error].find(
    (error): error is ApiRequestError => error instanceof ApiRequestError && [401, 503].includes(error.status),
  );

  if (authenticationError) return <AuthSetupPanel error={authenticationError} />;

  return (
    <main className="min-h-dvh">
      <header className="border-b border-[var(--border)] bg-[var(--surface)]">
        <div className="mx-auto flex h-14 max-w-[1500px] items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5 font-bold tracking-tight">
            <span className="grid size-7 place-items-center rounded-[5px] bg-[var(--accent)] text-[var(--surface)]"><GitCommitHorizontal size={17} /></span>
            <span>Patchwork<span className="ml-2 text-xs font-medium text-[var(--muted)]">DOC OPS</span></span>
          </Link>
          <div className="flex items-center gap-3 text-xs text-[var(--muted)]">
            <span className="hidden items-center gap-1.5 sm:flex"><span className="size-2 rounded-full bg-[var(--accent)]" />API connected</span>
            <Badge className="border-[var(--border)] bg-[var(--surface-muted)] text-[var(--foreground)]">
              <ShieldCheck size={12} />{userQuery.data?.role ?? "…"}
            </Badge>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] px-4 pb-10 pt-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mb-1 flex items-center gap-2 text-[11px] font-bold uppercase text-[var(--accent)]">
              <span className="size-1.5 rounded-full bg-current" />Review queue
            </div>
            <h1 className="text-2xl font-bold tracking-tight">Documentation sessions</h1>
            <p className="mt-1 text-sm text-[var(--muted)]">Evidence-backed patches generated from repository changes.</p>
          </div>
          {userQuery.data?.role === "WRITER" && <CreateSessionDialog />}
        </div>

        <div className="mb-4 grid grid-cols-2 gap-3 sm:max-w-[460px]">
          <Card className="flex items-center justify-between px-4 py-3">
            <div><div className="text-[11px] uppercase text-[var(--muted)]">Awaiting review</div><div className="mt-1 text-xl font-bold tabular-nums">{awaiting}</div></div>
            <CircleDot className="text-[var(--warning)]" size={18} />
          </Card>
          <Card className="flex items-center justify-between px-4 py-3">
            <div><div className="text-[11px] uppercase text-[var(--muted)]">Decided</div><div className="mt-1 text-xl font-bold tabular-nums">{complete}</div></div>
            <Clock3 className="text-[var(--accent)]" size={18} />
          </Card>
        </div>

        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-3 py-2.5 sm:px-4">
            <div className="flex flex-1 flex-wrap items-center gap-2">
              <label className="relative min-w-[210px] flex-1 sm:max-w-[340px]">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted)]" size={15} />
                <Input className="h-8 pl-8 text-xs" placeholder="Search source or head SHA" value={search} onChange={(event) => setSearch(event.target.value)} />
              </label>
              <select
                aria-label="Filter by status"
                className="h-8 rounded-[5px] border border-[var(--border)] bg-[var(--surface-raised)] px-2 text-xs text-[var(--foreground)]"
                value={status}
                onChange={(event) => setStatus(event.target.value as "ALL" | ReviewStatus)}
              >
                <option value="ALL">All statuses</option>
                <option value="AWAITING_REVIEW">Awaiting review</option>
                <option value="APPROVED">Approved</option>
                <option value="REJECTED">Rejected</option>
              </select>
            </div>
            <span className="text-xs tabular-nums text-[var(--muted)]">{sessions.length} sessions</span>
          </div>

          {sessionsQuery.isPending ? (
            <div className="grid min-h-56 place-items-center text-sm text-[var(--muted)]">Loading sessions…</div>
          ) : sessionsQuery.isError ? (
            <div className="grid min-h-56 place-items-center px-4 text-center">
              <div><p className="font-semibold">Could not load sessions</p><p className="mt-1 text-sm text-[var(--muted)]">{sessionsQuery.error.message}</p></div>
            </div>
          ) : sortedSessions.length === 0 ? (
            <div className="grid min-h-56 place-items-center px-4 text-center">
              <div><p className="font-semibold">No matching sessions</p><p className="mt-1 text-sm text-[var(--muted)]">Adjust the search or create a review from a commit range.</p></div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table className="min-w-[820px]">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>{sortHeading("Session ID", "id")}</TableHead>
                    <TableHead>{sortHeading("Source commit", "source_commit")}</TableHead>
                    <TableHead>{sortHeading("Status", "status")}</TableHead>
                    <TableHead>{sortHeading("Creator", "creator")}</TableHead>
                    <TableHead>{sortHeading("Last updated", "updated_at")}</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedSessions.map((session) => (
                    <TableRow key={session.id}>
                      <TableCell className="mono text-xs text-[var(--muted)]">{session.id.slice(0, 8)}</TableCell>
                      <TableCell className="mono text-xs" title={session.source_commit}>{session.source_commit.slice(0, 12)}</TableCell>
                      <TableCell><StatusBadge status={session.status} /></TableCell>
                      <TableCell>{session.creator.username}</TableCell>
                      <TableCell className="text-xs text-[var(--muted)]">{formatDate(session.updated_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button asChild variant="secondary" size="sm"><Link href={`/sessions/${session.id}`}>View Review</Link></Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </Card>
      </div>
    </main>
  );
}