import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  History as HistoryIcon,
  Trash2,
  CircleCheck,
  CircleX,
  Calendar,
  DollarSign,
  Percent,
  Shield,
  Zap
} from "lucide-react";
import { apiRequest } from "@/lib/queryClient";
import { PageHeader } from "@/components/terminal";
import { useToast } from "@/hooks/use-toast";
import type { ArbitrageHistory } from "@shared/schema";
import { format } from "date-fns";

export default function HistoryPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: history, isLoading } = useQuery<ArbitrageHistory[]>({
    queryKey: ['/api/arbitrage-history']
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      return apiRequest('DELETE', `/api/arbitrage-history/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['/api/arbitrage-history'] });
      toast({
        title: "Deleted",
        description: "Entry removed from history.",
      });
    }
  });

  const clearAllMutation = useMutation({
    mutationFn: async () => {
      return apiRequest('DELETE', '/api/arbitrage-history');
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['/api/arbitrage-history'] });
      toast({
        title: "Cleared",
        description: "All history entries removed.",
      });
    }
  });

  return (
    <div className="min-h-full bg-background">
      <div className="reveal-stack max-w-4xl mx-auto px-4 py-8 md:py-12">
        <div className="mb-8">
          <PageHeader
            index="07"
            kicker="LOG // TRADE HISTORY"
            title="Arbitrage History"
            description="Review your saved arbitrage opportunities"
            icon={HistoryIcon}
          >
            {history && history.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => clearAllMutation.mutate()}
                disabled={clearAllMutation.isPending}
                className="font-mono text-xs uppercase tracking-widest"
                data-testid="button-clear-all"
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Clear All
              </Button>
            )}
          </PageHeader>
        </div>

        {isLoading && (
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <Card key={i} className="animate-pulse">
                <CardContent className="py-6">
                  <div className="h-4 bg-muted rounded w-1/3 mb-4" />
                  <div className="h-3 bg-muted rounded w-1/2 mb-2" />
                  <div className="h-3 bg-muted rounded w-1/4" />
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {!isLoading && (!history || history.length === 0) && (
          <Card className="border-dashed">
            <CardContent className="py-12 text-center">
              <div className="flex flex-col items-center gap-3">
                <div className="p-3 rounded-full bg-muted">
                  <HistoryIcon className="w-6 h-6 text-muted-foreground" />
                </div>
                <div>
                  <p className="text-muted-foreground font-medium">
                    No history yet
                  </p>
                  <p className="text-sm text-muted-foreground/70">
                    Save arbitrage opportunities from the calculator to see them here
                  </p>
                </div>
                <Button
                  variant="outline"
                  onClick={() => window.location.href = '/'}
                  className="mt-2"
                  data-testid="button-go-calculator"
                >
                  Go to Calculator
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {!isLoading && history && history.length > 0 && (
          <div className="space-y-4">
            {history.map((entry) => (
              <Card 
                key={entry.id}
                className={`${
                  entry.isProfitable 
                    ? "ring-1 ring-green-500/20 dark:ring-green-400/20" 
                    : "ring-1 ring-red-500/20 dark:ring-red-400/20"
                }`}
                data-testid={`card-history-${entry.id}`}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-2 flex-wrap">
                    <div>
                      <CardTitle className="text-base font-semibold flex items-center gap-2">
                        {entry.siteAName} vs {entry.siteBName}
                        {entry.isProfitable ? (
                          <Badge className="bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20">
                            <CircleCheck className="w-3 h-3 mr-1" />
                            Profit
                          </Badge>
                        ) : (
                          <Badge className="bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20">
                            <CircleX className="w-3 h-3 mr-1" />
                            Loss
                          </Badge>
                        )}
                      </CardTitle>
                      <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
                        <Calendar className="w-3 h-3" />
                        {entry.createdAt ? format(new Date(entry.createdAt), "MMM d, yyyy 'at' h:mm a") : "—"}
                        <span className="mx-1">|</span>
                        {entry.orderMode === "Maker" ? (
                          <span className="flex items-center gap-1">
                            <Shield className="w-3 h-3" /> Maker
                          </span>
                        ) : (
                          <span className="flex items-center gap-1">
                            <Zap className="w-3 h-3" /> Taker
                          </span>
                        )}
                      </div>
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => deleteMutation.mutate(entry.id)}
                      disabled={deleteMutation.isPending}
                      data-testid={`button-delete-${entry.id}`}
                    >
                      <Trash2 className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Prices</p>
                      <p className="font-mono text-sm">
                        {entry.siteAYesPrice.toFixed(2)} / {entry.siteBYesPrice.toFixed(2)}
                      </p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground flex items-center gap-1">
                        <DollarSign className="w-3 h-3" /> Investment
                      </p>
                      <p className="font-mono text-sm font-semibold">
                        ${entry.investment.toFixed(2)}
                      </p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Net Profit</p>
                      <p className={`font-mono text-sm font-bold tabular-nums ${
                        (entry.netProfit ?? 0) > 0 ? "value-pos" : "value-neg"
                      }`}>
                        {(entry.netProfit ?? 0) > 0 ? "+" : ""}${(entry.netProfit ?? 0).toFixed(2)}
                      </p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground flex items-center gap-1">
                        <Percent className="w-3 h-3" /> ROI
                      </p>
                      <p className={`font-mono text-sm font-bold tabular-nums ${
                        (entry.netRoi ?? 0) > 0 ? "value-pos" : "value-neg"
                      }`}>
                        {(entry.netRoi ?? 0).toFixed(2)}%
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 pt-3 border-t border-border text-sm text-muted-foreground">
                    {entry.scenario} | {entry.shares} shares
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
