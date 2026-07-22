import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Shared "tactical terminal" primitives. Every page header and KPI row in the
 * app renders through these so the whole product reads as one instrument.
 */

interface PageHeaderProps {
  /** Two-digit module index shown in the kicker, e.g. "01" */
  index: string;
  /** Mono uppercase kicker label, e.g. "MODULE // ARBITRAGE RADAR" */
  kicker: string;
  title: string;
  description?: string;
  icon?: LucideIcon;
  /** Right-aligned slot for actions or live readouts */
  children?: ReactNode;
}

export function PageHeader({ index, kicker, title, description, icon: Icon, children }: PageHeaderProps) {
  return (
    <header className="space-y-3">
      <div className="section-rule">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-primary tabular-nums">
          {index}
        </span>
        <span className="block-cursor font-mono text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
          {kicker}
        </span>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="space-y-1.5">
          <h1 className="flex items-center gap-3 text-3xl font-black tracking-tight sm:text-4xl">
            {Icon && <Icon className="size-7 text-primary" strokeWidth={1.5} />}
            {title}
          </h1>
          {description && (
            <p className="font-mono text-xs text-muted-foreground sm:text-sm">{description}</p>
          )}
        </div>
        {children && <div className="flex items-center gap-2">{children}</div>}
      </div>
    </header>
  );
}

interface StatTileProps {
  label: string;
  value: ReactNode;
  /** Small mono annotation under the value, e.g. "24H" or "vs. model" */
  detail?: ReactNode;
  icon?: LucideIcon;
  /** Reserved status accent — renders a colored dot beside the label, never colors the value */
  status?: "positive" | "negative" | "neutral";
  className?: string;
}

const statusDot: Record<NonNullable<StatTileProps["status"]>, string> = {
  positive: "bg-chart-1",
  negative: "bg-destructive",
  neutral: "bg-muted-foreground",
};

export function StatTile({ label, value, detail, icon: Icon, status, className }: StatTileProps) {
  return (
    <div className={cn("hud-corners border border-card-border bg-card px-4 py-3", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
          {status && <span className={cn("size-1.5 rounded-full", statusDot[status])} />}
          {label}
        </span>
        {Icon && <Icon className="size-3.5 text-muted-foreground" strokeWidth={1.5} />}
      </div>
      <div className="mt-1.5 font-mono text-2xl font-bold tabular-nums text-foreground sm:text-3xl">
        {value}
      </div>
      {detail && (
        <div className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          {detail}
        </div>
      )}
    </div>
  );
}

/** Platform identity — a mono tag with a fixed accent bar, replacing gradient headers */
const PLATFORM_ACCENT: Record<string, string> = {
  kalshi: "hsl(var(--chart-2))",
  polymarket: "hsl(var(--chart-4))",
  predictit: "hsl(var(--chart-1))",
  ibkr: "hsl(var(--chart-3))",
};

export function platformAccent(platform: string): string {
  return PLATFORM_ACCENT[platform.toLowerCase()] ?? "hsl(var(--muted-foreground))";
}

export function PlatformTag({ platform, className }: { platform: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border border-border bg-secondary/50 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.2em]",
        className,
      )}
    >
      <span className="size-1.5" style={{ backgroundColor: platformAccent(platform) }} />
      {platform}
    </span>
  );
}
