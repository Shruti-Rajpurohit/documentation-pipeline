"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { diffLines } from "diff";
import {
  ArrowLeft,
  CheckCheck,
  Clock3,
  FileCode2,
  FileText,
  LoaderCircle,
  RotateCw,
  Save,
  ShieldCheck,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiRequestError, approveSession, editSession, getCurrentUser, getSession, rejectSession } from "@/lib/api";
import type { ReviewStatus } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { AuthSetupPanel } from "@/components/auth-setup-panel";

type Pane = "context" | "current" | "proposed";

const statusStyles: Record<ReviewStatus, string> = {
  AWAITING_REVIEW: "border-[var(--warning)]/40 bg-[var(--warning-soft)] text-[var(--warning)]",
  APPROVED: "border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[var(--accent)]",
  REJECTED: "border-[var(--danger)]/40 bg-[var(--danger-soft)] text-[var(--danger)]",
};

function shortSha(value: string) {
  return value.length > 12 ? value.slice(0, 12) : value;
}

function dateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function DiffText({ before, after }: { before: string; after: string }) {
  const parts = diffLines(before, after);
  return (
    <pre className="code-lines min-h-full p-3">
      {parts.map((part, index) => {
        const className = part.added ? "diff-add" : part.removed ? "diff-remove" : "diff-context";
        const prefix = part.added ? "+ " : part.removed ? "− " : "  ";
        return part.value.split(/(?<=\n)/).filter(Boolean).map((line, lineIndex) => (
          <span key={`${index}-${lineIndex}`} className={`block px-2 ${className}`}>
            {prefix}{line.replace(/\n$/, "") || " "}
          </span>
        ));
      })}
    </pre>
  );
}

function SourceDiff({ value }: { value: string }) {
  return (
    <pre className="code-lines min-h-full p-3">
      {value.split("\n").map((line, index) => {
        const className = line.startsWith("+") && !line.startsWith("+++")
          ? "diff-add"
          : line.startsWith("-") && !line.startsWith("---")
            ? "diff-remove"
            : "diff-context";
        return <span key={index} className={`block min-h-[1.65em] px-2 ${className}`}>{line || " "}</span>;
      })}
    </pre>
  );
}

function PaneTitle({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <CardHeader className="min-h-[52px] justify-start">
      <span className="text-[var(--accent)]">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs font-bold uppercase">{title}</div>
        <div className="truncate text-[11px] text-[var(--muted)]">{detail}</div>
      </div>
    </CardHeader>
  );
}

export function ReviewWorkspace() {
  const params = useParams<{ id: string }>();
  const sessionId = params.id;
  const queryClient = useQueryClient();
  const [selectedEditId, setSelectedEditId] = useState<number | null>(null);
  const [mobilePane, setMobilePane] = useState<Pane>("context");
  const [editorDraft, setEditorDraft] = useState<{ editId: number; content: string } | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const sessionQuery = useQuery({ queryKey: ["session", sessionId], queryFn: () => getSession(sessionId) });
  const userQuery = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser });
  const session = sessionQuery.data;
  const edits = session?.proposal?.edits ?? [];
  const activeEdit = edits.find((edit) => edit.id === selectedEditId) ?? edits[0];
  const editedContent = activeEdit && editorDraft?.editId === activeEdit.id
    ? editorDraft.content
    : activeEdit?.content ?? "";
  const pending = session?.status === "AWAITING_REVIEW";
  const role = userQuery.data?.role;
  const authenticationError = [sessionQuery.error, userQuery.error].find(
    (error): error is ApiRequestError => error instanceof ApiRequestError && [401, 503].includes(error.status),
  );

  const saveMutation = useMutation({
    mutationFn: () => activeEdit && editedContent.trim()
      ? editSession(sessionId, activeEdit.id, editedContent)
      : Promise.reject(new Error("Select an edit and enter non-empty content")),
    onSuccess: async () => {
      toast.success("Patch edit saved", { description: "The change history has been updated." });
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
    },
    onError: (error) => toast.error("Could not save edit", { description: error.message }),
  });
  const approveMutation = useMutation({
    mutationFn: () => approveSession(sessionId),
    onSuccess: async () => {
      toast.success("Patch approved and applied");
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (error) => toast.error("Approval failed", { description: error.message }),
  });
  const rejectMutation = useMutation({
    mutationFn: () => rejectSession(sessionId, rejectReason.trim() || undefined),
    onSuccess: async () => {
      toast.success("Review rejected");
      await queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (error) => toast.error("Rejection failed", { description: error.message }),
  });

  if (sessionQuery.isPending) {
    return <main className="grid min-h-dvh place-items-center text-sm text-[var(--muted)]"><LoaderCircle className="mr-2 animate-spin" size={16} />Loading review session…</main>;
  }
  if (authenticationError) return <AuthSetupPanel error={authenticationError} />;
  if (sessionQuery.isError || !session) {
    return (
      <main className="mx-auto grid min-h-dvh max-w-xl place-items-center p-6 text-center">
        <div><p className="font-semibold">Review session unavailable</p><p className="mt-1 text-sm text-[var(--muted)]">{sessionQuery.error?.message ?? "The session could not be loaded."}</p><Button asChild variant="secondary" className="mt-4"><Link href="/">Back to sessions</Link></Button></div>
      </main>
    );
  }

  const paneVisibility = (pane: Pane) => mobilePane === pane ? "flex" : "hidden";

  return (
    <main className="flex min-h-dvh flex-col">
      <header className="flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="Back to sessions"><Link href="/"><ArrowLeft size={17} /></Link></Button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-sm font-bold">Review workspace</h1>
              <Badge className={statusStyles[session.status]}>{session.status.replaceAll("_", " ").toLowerCase()}</Badge>
            </div>
            <div className="mono mt-0.5 truncate text-[10px] text-[var(--muted)]">{session.id} · {shortSha(session.source_commit)} → {shortSha(session.head_commit)}</div>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void sessionQuery.refetch()}><RotateCw size={14} />Refresh</Button>
      </header>

      <div className="flex items-center gap-1 border-b border-[var(--border)] bg-[var(--surface-muted)] px-3 py-1.5 md:hidden">
        {(["context", "current", "proposed"] as const).map((pane) => (
          <button key={pane} type="button" onClick={() => setMobilePane(pane)} className={`flex-1 rounded-[4px] px-2 py-1.5 text-xs font-semibold capitalize ${mobilePane === pane ? "bg-[var(--surface)] text-[var(--foreground)] shadow-sm" : "text-[var(--muted)]"}`}>
            {pane === "context" ? "Source" : pane === "current" ? "Current doc" : "Proposal"}
          </button>
        ))}
      </div>

      <div className="grid flex-1 grid-cols-1 gap-2 p-2 md:min-h-[520px] md:grid-cols-3 md:p-3">
        <Card className={`min-h-[400px] flex-col overflow-hidden md:flex ${paneVisibility("context")}`}>
          <PaneTitle icon={<FileCode2 size={15} />} title="Source diff" detail={`${session.changed_files.length} changed files`} />
          <div className="scroll-panel flex-1 bg-[var(--code-bg)]"><SourceDiff value={session.source_diff} /></div>
        </Card>

        <Card className={`min-h-[400px] flex-col overflow-hidden md:flex ${paneVisibility("current")}`}>
          <PaneTitle icon={<FileText size={15} />} title="Current document" detail={activeEdit?.file_path ?? "No document selected"} />
          {edits.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-[var(--border)] px-2 py-1.5">
              {edits.map((edit) => (
                <button key={edit.id} type="button" onClick={() => setSelectedEditId(edit.id)} className={`shrink-0 rounded-[4px] px-2 py-1 text-[10px] ${activeEdit?.id === edit.id ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--muted)] hover:bg-[var(--surface-muted)]"}`}>
                  {edit.section_heading}
                </button>
              ))}
            </div>
          )}
          <div className="scroll-panel flex-1 bg-[var(--code-bg)] p-3">
            <pre className="code-lines whitespace-pre-wrap text-[var(--foreground)]">{activeEdit?.original_content || "No original document content was returned."}</pre>
          </div>
          {activeEdit && <div className="border-t border-[var(--border)] px-3 py-2 text-[10px] text-[var(--muted)]">Section starts at line {activeEdit.start_line ?? "unknown"}</div>}
        </Card>

        <Card className={`min-h-[400px] flex-col overflow-hidden md:flex ${paneVisibility("proposed")}`}>
          <PaneTitle icon={<FileText size={15} />} title="Proposed patch" detail={activeEdit?.section_heading ?? "No proposal"} />
          {edits.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-[var(--border)] px-2 py-1.5">
              {edits.map((edit) => (
                <button key={edit.id} type="button" onClick={() => setSelectedEditId(edit.id)} className={`shrink-0 rounded-[4px] px-2 py-1 text-[10px] ${activeEdit?.id === edit.id ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--muted)] hover:bg-[var(--surface-muted)]"}`}>
                  {edit.file_path.split("/").at(-1)}
                </button>
              ))}
            </div>
          )}
          {activeEdit ? (
            <div className="scroll-panel flex-1 bg-[var(--code-bg)]">
              <DiffText before={activeEdit.original_content} after={activeEdit.content} />
            </div>
          ) : (
            <div className="scroll-panel flex-1 p-3 text-sm text-[var(--muted)]">No proposal content is available.</div>
          )}
          <details open className="border-t border-[var(--border)]">
            <summary className="cursor-pointer px-3 py-2 text-[11px] font-semibold text-[var(--muted)]">Edit proposed content</summary>
            <Textarea
              aria-label="Proposed documentation content"
              className="mono min-h-44 rounded-none border-0 border-t border-[var(--border)] bg-[var(--surface-raised)] text-xs"
              readOnly={!activeEdit || role !== "WRITER" || !pending}
              value={editedContent}
              onChange={(event) => activeEdit && setEditorDraft({ editId: activeEdit.id, content: event.target.value })}
            />
          </details>
        </Card>
      </div>

      <div className="sticky bottom-0 z-20 border-t border-[var(--border)] bg-[var(--surface)] px-3 py-3 sm:px-5">
        <div className="mx-auto grid max-w-[1600px] gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div className="min-w-0">
            <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase text-[var(--muted)]"><Clock3 size={12} />Change history</div>
            <div className="flex max-h-20 gap-2 overflow-x-auto pb-1">
              {session.audit_events.length === 0 ? (
                <span className="text-xs text-[var(--muted)]">No edits recorded yet.</span>
              ) : session.audit_events.map((event) => (
                <div key={event.id} className="min-w-[220px] max-w-[340px] shrink-0 rounded-[5px] border border-[var(--border)] bg-[var(--surface-raised)] px-2.5 py-1.5">
                  <div className="flex items-center justify-between gap-2 text-[10px]"><span className="font-bold">{event.actor_username}</span><time className="text-[var(--muted)]">{dateTime(event.created_at)}</time></div>
                  <div className="mt-1 truncate text-[10px] text-[var(--muted)]">{event.previous_content} → {event.updated_content}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {role === "REVIEWER" && pending && (
              <>
                <input
                  aria-label="Rejection reason"
                  value={rejectReason}
                  onChange={(event) => setRejectReason(event.target.value)}
                  placeholder="Optional rejection reason"
                  className="h-9 min-w-[180px] rounded-[5px] border border-[var(--border)] bg-[var(--surface-raised)] px-2.5 text-xs outline-none focus:border-[var(--accent)]"
                />
                <Button variant="danger" disabled={rejectMutation.isPending} onClick={() => rejectMutation.mutate()}><X size={15} />Reject</Button>
              </>
            )}
            {role === "WRITER" && pending && (
              <Button variant="secondary" disabled={!activeEdit || !editedContent.trim() || editedContent === activeEdit.content || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                {saveMutation.isPending ? <LoaderCircle className="animate-spin" size={15} /> : <Save size={15} />}Save Edit
              </Button>
            )}
            {role === "APPROVER" && pending && (
              <Button disabled={approveMutation.isPending} onClick={() => approveMutation.mutate()}>
                {approveMutation.isPending ? <LoaderCircle className="animate-spin" size={15} /> : <CheckCheck size={15} />}Approve &amp; Apply
              </Button>
            )}
            {!pending && <span className="flex items-center gap-1.5 text-xs text-[var(--muted)]"><ShieldCheck size={14} />Review closed</span>}
            {pending && !role && <span className="text-xs text-[var(--muted)]">Resolving role…</span>}
          </div>
        </div>
      </div>
    </main>
  );
}