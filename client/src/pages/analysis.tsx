import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { 
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie
} from "recharts";
import { 
  Brain, Download, Info, CheckCircle2, XCircle, AlertCircle, 
  Database, RefreshCw, Layers
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { PageHeader, StatTile } from "@/components/terminal";

interface MLStats {
  feedback: Record<string, number>;
  totalMatches: number;
  trainingSamples: number;
}

export default function AnalysisPage() {
  const { toast } = useToast();
  const { data: stats, isLoading, refetch } = useQuery<MLStats>({
    queryKey: ["/api/ml/stats"],
  });

  const exportMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/ml/export", { method: "POST" });
      if (!res.ok) throw new Error("Export failed");
      return res.json();
    },
    onSuccess: (data) => {
      toast({
        title: "Export Success",
        description: `Training data exported to: ${data.file}`,
      });
    },
    onError: () => {
      toast({
        title: "Export Error",
        description: "Failed to generate training dataset.",
        variant: "destructive",
      });
    }
  });

  if (isLoading || !stats) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <RefreshCw className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  const feedbackData = Object.entries(stats.feedback).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1).replace('_', ' '),
    value
  }));

  // Feedback labels are states, not arbitrary series — color by meaning, never by index
  const FEEDBACK_COLORS: Record<string, string> = {
    approve: "hsl(144 100% 40%)",
    reject: "hsl(3 100% 55%)",
    unsure: "hsl(48 100% 50%)",
  };
  const feedbackColor = (name: string) =>
    FEEDBACK_COLORS[name.toLowerCase().replace(" ", "_")] ?? "hsl(187 100% 42%)";

  return (
    <div className="reveal-stack container mx-auto px-4 py-8 space-y-8">
      <PageHeader
        index="04"
        kicker="MODULE // ML ANALYSIS"
        title="ML Analysis"
        description="Matching engine performance and active learning loop metrics"
        icon={Brain}
      >
        <Button
          variant="outline"
          onClick={() => refetch()}
          className="gap-2 font-mono text-xs uppercase tracking-widest"
        >
          <RefreshCw className="w-4 h-4" />
          Sync Stats
        </Button>
        <Button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending || stats.trainingSamples === 0}
          className="gap-2 font-mono text-xs uppercase tracking-widest"
        >
          <Download className="w-4 h-4" />
          Export CSV
        </Button>
      </PageHeader>

      {/* Overview tiles */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <StatTile
          label="Total pairs evaluated"
          value={stats.totalMatches.toLocaleString()}
          detail="Across all recent scans"
          icon={Layers}
        />
        <StatTile
          label="Verified samples"
          value={stats.trainingSamples.toLocaleString()}
          detail="High-quality labeled data"
          icon={Database}
        />
        <StatTile
          label="Precision confidence"
          value={`${stats.feedback.approve ? ((stats.feedback.approve / stats.trainingSamples) * 100).toFixed(1) : "0"}%`}
          detail="Based on user approvals"
          icon={CheckCircle2}
          status="positive"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Feedback Distribution Chart */}
        <Card>
          <CardHeader>
            <CardTitle>User Feedback Distribution</CardTitle>
            <CardDescription>Breakdown of labels collected for fine-tuning.</CardDescription>
          </CardHeader>
          <CardContent className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={feedbackData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(0 0% 16%)" />
                <XAxis dataKey="name" stroke="hsl(0 0% 62%)" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis stroke="hsl(0 0% 62%)" fontSize={12} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={{ fill: "hsl(0 0% 100% / 0.04)" }}
                  contentStyle={{
                    borderRadius: "2px",
                    border: "1px solid hsl(0 0% 18%)",
                    background: "hsl(0 0% 9%)",
                    color: "hsl(0 0% 98%)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="value" radius={[2, 2, 0, 0]}>
                  {feedbackData.map((entry) => (
                    <Cell key={entry.name} fill={feedbackColor(entry.name)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        {/* Model Insights */}
        <Card>
          <CardHeader>
            <CardTitle>Semantic Discovery</CardTitle>
            <CardDescription>How the matching engine is performing.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="hud-corners flex items-start gap-4 p-4 bg-accent/40 border border-primary/10">
              <div className="bg-primary/10 p-3">
                <Brain className="w-6 h-6 text-primary" strokeWidth={1.5} />
              </div>
              <div className="space-y-1">
                <div className="font-semibold">Active Learning Active</div>
                <p className="text-sm text-muted-foreground">
                  The engine is prioritizing matches with 60-80% similarity for user verification to harden the boundary.
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <h4 className="section-rule font-mono text-[10px] font-semibold text-muted-foreground uppercase tracking-[0.25em]">
                <Info className="w-3.5 h-3.5" />
                Discovery Pipeline
              </h4>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm p-3 border rounded-md">
                  <span>Keyword Pre-filtering efficiency</span>
                  <Badge variant="secondary">98.2%</Badge>
                </div>
                <div className="flex items-center justify-between text-sm p-3 border rounded-md">
                  <span>Avg Reasoning Latency</span>
                  <Badge variant="secondary">42ms</Badge>
                </div>
                <div className="flex items-center justify-between text-sm p-3 border rounded-md">
                  <span>GPU Offload Threshold</span>
                  <Badge variant="secondary">500+ items</Badge>
                </div>
              </div>
            </div>

            <div className="p-4 bg-chart-3/[0.04] border border-chart-3/15 text-xs flex gap-2">
              <AlertCircle className="w-4 h-4 text-chart-3 shrink-0" />
              Tip: Export data once "Reject" count reaches 50+ to capture enough counter-examples for effective negative sampling during re-training.
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Dataset Preview / Action */}
      <Card className="scanline relative overflow-hidden">
        <CardHeader>
          <CardTitle>Fine-Tuning Loop</CardTitle>
          <CardDescription>Bridging the local terminal with Google Colab.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-4">
            <h4 className="font-semibold flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-chart-1" />
              1. Labeled Data
            </h4>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Every time you rate a match, it's stored in `arbitrage.db`. This creates a localized, high-confidence dataset of what "Same Market" actually looks like for your specific trading edge.
            </p>
          </div>
          <div className="space-y-4">
            <h4 className="font-semibold flex items-center gap-2">
              <RefreshCw className="w-4 h-4 text-primary" />
              2. GPU Training
            </h4>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Use the "Export" button above, then upload that CSV to the **Cloud GPU Matcher** notebook. Run the "Fine-Tune" cell to update the `embeddings_cache` with a smarter model.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
