import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex min-h-9 items-center justify-center gap-2 whitespace-nowrap rounded-[5px] px-3 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-[var(--accent)] text-[var(--surface)] hover:brightness-110",
        secondary: "border border-[var(--border)] bg-[var(--surface-raised)] text-[var(--foreground)] hover:bg-[var(--surface-muted)]",
        ghost: "text-[var(--muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--foreground)]",
        danger: "bg-[var(--danger)] text-white hover:brightness-110",
        outline: "border border-[var(--border-strong)] bg-transparent text-[var(--foreground)] hover:bg-[var(--surface-muted)]",
      },
      size: {
        default: "h-9",
        sm: "h-8 px-2.5 text-xs",
        icon: "size-9 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Component = asChild ? Slot : "button";
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}