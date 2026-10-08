"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({ className, children, ...props }: ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/55 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        className={cn("fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[min(560px,calc(100vw-28px))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[7px] border border-[var(--border-strong)] bg-[var(--surface-raised)] p-5 shadow-[var(--shadow)] focus:outline-none", className)}
        {...props}
      >
        {children}
        <DialogPrimitive.Close className="absolute right-3 top-3 inline-flex size-8 items-center justify-center rounded-[4px] text-[var(--muted)] hover:bg-[var(--surface-muted)]" aria-label="Close dialog">
          <X size={16} />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogTitle(props: ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className="text-lg font-bold" {...props} />;
}

export function DialogDescription(props: ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className="mt-1 text-sm text-[var(--muted)]" {...props} />;
}