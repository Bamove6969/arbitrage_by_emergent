import { Link } from "wouter";

export default function NotFound() {
  return (
    <div className="terminal-grid-bg flex min-h-full w-full flex-col items-center justify-center gap-8 p-8">
      <div className="section-rule w-full max-w-md">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-destructive">
          ERR // ROUTE NOT MAPPED
        </span>
      </div>

      <div className="text-center">
        <div
          className="glitch font-black tracking-tighter text-8xl sm:text-9xl"
          data-text="404"
          aria-hidden="true"
        >
          404
        </div>
        <h1 className="block-cursor mt-2 font-mono text-sm uppercase tracking-[0.4em] text-muted-foreground">
          Signal lost
        </h1>
      </div>

      <div className="hud-corners w-full max-w-md border border-card-border bg-card p-4">
        <p className="font-mono text-xs leading-relaxed text-muted-foreground">
          <span className="text-primary">$</span> trace --route
          <br />
          <span className="text-destructive">×</span> no module bound to this address
          <br />
          <span className="text-chart-1">✓</span> uplink to terminal still active
        </p>
      </div>

      <Link
        href="/sentinel"
        className="hover-elevate border border-primary/40 bg-accent px-6 py-2.5 font-mono text-xs uppercase tracking-[0.25em] text-accent-foreground"
        data-testid="link-return-radar"
      >
        ▸ Return to radar
      </Link>
    </div>
  );
}
