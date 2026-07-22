import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { PageHeader, StatTile } from "@/components/terminal";
import {
  Workflow,
  Cpu,
  Bot,
  CheckCircle2,
  XCircle,
  CircleDashed,
  Loader2,
  Database,
  Target,
} from "lucide-react";

/* ── Types mirrored from backend/scanner.py + backend/live_state.py ── */

interface KaggleStage {
  index: number;
  name: string;
  status: "pending" | "running" | "done" | "error";
  message: string;
  started_at: string | null;
  ended_at: string | null;
}

interface KaggleState {
  running: boolean;
  kernel: string | null;
  current_stage: number | null;
  started_at: string | null;
  updated_at: string | null;
  stages: KaggleStage[];
}

interface LlmInstance {
  model: string;
  workers: number;
  active: number;
  done: number;
  exact: number;
  total: number;
  remaining: number;
}

interface LlmState {
  running: boolean;
  received: number;
  total_exact: number;
  started_at: string | null;
  finished_at: string | null;
  instances: LlmInstance[];
}

/* ── Helpers ── */

function elapsed(start: string | null, end: string | null): string {
  if (!start) return "";
  const s = new Date(start + "Z").getTime();
  const e = end ? new Date(end + "Z").getTime() : Date.now();
  const secs = Math.max(0, Math.round((e - s) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function NotebookProgress({ percent, error }: { percent: number; error?: boolean }) {
  const clamped = Math.min(100, Math.max(0, Math.round(percent)));
  return (
    <div className="flex items-center gap-3 pt-3">
      <div className="h-1.5 flex-1 overflow-hidden bg-muted">
        <div
          className={`h-full transition-[width] duration-700 ease-out ${
            error ? "bg-destructive" : clamped >= 100 ? "bg-chart-1" : "bg-primary"
          }`}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-xs font-bold tabular-nums">
        {clamped}%
      </span>
    </div>
  );
}

function StageGlyph({ status }: { status: KaggleStage["status"] }) {
  switch (status) {
    case "done":
      return <CheckCircle2 className="size-4 text-chart-1" strokeWidth={1.5} />;
    case "error":
      return <XCircle className="size-4 text-destructive" strokeWidth={1.5} />;
    case "running":
      return <Loader2 className="size-4 animate-spin text-primary" strokeWidth={2} />;
    default:
      return <CircleDashed className="size-4 text-muted-foreground/50" strokeWidth={1.5} />;
  }
}

/* ── Notebook 1: Cloud GPU Matcher — cell-by-cell timeline ── */

function MatcherPanel({ state }: { state?: KaggleState }) {
  const stages = state?.stages ?? [];
  const isLive = state?.running ?? false;
  const hasError = stages.some((s) => s.status === "error");
  // a running cell counts as half a cell so the bar moves as soon as work starts
  const percent = stages.length
    ? (stages.filter((s) => s.status === "done").length * 100 +
        stages.filter((s) => s.status === "running").length * 50) /
      stages.length
    : 0;

  return (
    <Card className={isLive ? "tracing-beam" : undefined}>
      <CardHeader className="border-b bg-muted/20">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Cpu className="size-5 text-primary" strokeWidth={1.5} />
          NB 01 · Cloud GPU Matcher
          {isLive && (
            <Badge className="ml-auto animate-pulse font-mono text-[10px] uppercase tracking-widest">
              Executing
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="font-mono text-xs">
          {state?.kernel ?? "Cloud_GPU_Matcher_v4_Stable.ipynb"}
          {state?.started_at && ` · started ${elapsed(state.started_at, state.updated_at)} of activity ago`}
        </CardDescription>
        {(isLive || percent > 0) && <NotebookProgress percent={percent} error={hasError} />}
      </CardHeader>
      <CardContent className="p-0">
        {stages.length === 0 ? (
          <div className="p-8 text-center font-mono text-xs uppercase tracking-widest text-muted-foreground">
            Awaiting first beacon from notebook
          </div>
        ) : (
          <ol className="divide-y divide-border/60">
            {stages.map((stage) => {
              const isActive = stage.status === "running";
              return (
                <li
                  key={stage.index}
                  className={`relative flex items-start gap-3 px-4 py-3 transition-colors ${
                    isActive ? "bg-accent/40" : stage.status === "pending" ? "opacity-50" : ""
                  }`}
                  data-testid={`pipeline-stage-${stage.index}`}
                >
                  {isActive && <span className="absolute left-0 top-0 h-full w-0.5 bg-primary" />}
                  <span className="mt-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
                    [{stage.index}]
                  </span>
                  <StageGlyph status={stage.status} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={`text-sm font-medium ${isActive ? "text-foreground" : ""}`}>
                        {stage.name}
                      </span>
                      {(stage.started_at || stage.ended_at) && (
                        <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                          {elapsed(stage.started_at, stage.ended_at)}
                        </span>
                      )}
                    </div>
                    {stage.message && (
                      <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                        {isActive && <span className="text-primary">▸ </span>}
                        {stage.message}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

/* ── Notebook 2: Ollama Verifier — per-model worker progress ── */

function VerifierPanel({ state }: { state?: LlmState }) {
  const isLive = state?.running ?? false;
  const instances = state?.instances ?? [];
  const totalWork = instances.reduce((sum, i) => sum + i.total, 0);
  const totalDone = instances.reduce((sum, i) => sum + i.done, 0);
  const percent = totalWork > 0 ? (totalDone / totalWork) * 100 : 0;

  return (
    <Card className={isLive ? "tracing-beam" : undefined}>
      <CardHeader className="border-b bg-muted/20">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Bot className="size-5 text-primary" strokeWidth={1.5} />
          NB 02 · Ollama Verifier
          {isLive && (
            <Badge className="ml-auto animate-pulse font-mono text-[10px] uppercase tracking-widest">
              Executing
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="font-mono text-xs">
          Ollama_Verifier_v1.ipynb · LLM exact-match verification
          {state?.started_at && !state.finished_at && ` · running ${elapsed(state.started_at, null)}`}
        </CardDescription>
        {(isLive || percent > 0) && <NotebookProgress percent={percent} />}
      </CardHeader>
      <CardContent className="space-y-4 p-4">
        <div className="grid grid-cols-2 gap-3">
          <StatTile
            label="Candidates received"
            value={(state?.received ?? 0).toLocaleString()}
            icon={Database}
          />
          <StatTile
            label="Exact matches"
            value={(state?.total_exact ?? 0).toLocaleString()}
            icon={Target}
            status={state && state.total_exact > 0 ? "positive" : undefined}
          />
        </div>

        {instances.length === 0 ? (
          <div className="border border-dashed border-border p-6 text-center font-mono text-xs uppercase tracking-widest text-muted-foreground">
            Verifier idle — waiting for matcher handoff
          </div>
        ) : (
          <div className="space-y-3">
            {instances.map((inst, i) => {
              const pct = inst.total > 0 ? Math.round((inst.done / inst.total) * 100) : 0;
              return (
                <div key={i} className="space-y-1.5" data-testid={`verifier-instance-${i}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-mono text-xs font-medium">{inst.model}</span>
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                      {inst.done}/{inst.total} · {inst.active} active · {inst.exact} exact
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden bg-muted">
                    <div
                      className="h-full bg-primary transition-[width] duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ── Live log tail (SSE /api/logs) ── */

function LogFeed() {
  const [lines, setLines] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const source = new EventSource("/api/logs");
    source.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (Array.isArray(data.logs)) setLines(data.logs.slice(-300));
      } catch {
        /* keep stream alive on a malformed frame */
      }
    };
    return () => source.close();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines]);

  return (
    <Card className="scanline relative overflow-hidden">
      <CardHeader className="border-b bg-muted/20">
        <CardTitle className="block-cursor font-mono text-xs uppercase tracking-[0.25em]">
          Execution feed
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div ref={scrollRef} className="h-64 overflow-y-auto bg-background/60 p-3">
          {lines.length === 0 ? (
            <p className="font-mono text-xs text-muted-foreground">Connecting to log stream…</p>
          ) : (
            lines.map((line, i) => (
              <p key={i} className="whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-muted-foreground">
                <span className="select-none text-primary/50">›</span> {line}
              </p>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/* ── Page ── */

export default function PipelinePage() {
  const { data: kaggle } = useQuery<KaggleState>({
    queryKey: ["/api/kaggle-status"],
    refetchInterval: 2000,
  });
  const { data: llm } = useQuery<LlmState>({
    queryKey: ["/api/llm-status"],
    refetchInterval: 2000,
  });

  return (
    <div className="reveal-stack container mx-auto space-y-6 px-4 py-6 max-w-7xl">
      <PageHeader
        index="05"
        kicker="MODULE // NOTEBOOK PIPELINE"
        title="Notebook Pipeline"
        description="Follow both notebooks cell-by-cell as the GPU matcher and LLM verifier execute"
        icon={Workflow}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <MatcherPanel state={kaggle} />
        <VerifierPanel state={llm} />
      </div>

      <LogFeed />
    </div>
  );
}
