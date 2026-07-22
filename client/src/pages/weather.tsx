import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CloudRain, Info, Wind, Thermometer, Droplets, TrendingUp, AlertCircle, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { PageHeader } from "@/components/terminal";
import { MarketBrowser } from "@/components/market-browser";
import { Badge } from "@/components/ui/badge";
import { useQuery } from "@tanstack/react-query";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";

interface WeatherEdge {
  market: any;
  analysis: {
    modelProb: number;
    marketProb: number;
    edge: number;
    recommendation: string;
    city: string;
    type: string;
  };
}

export default function WeatherTerminalPage() {
  const { data: edges, isLoading: loadingEdges } = useQuery<WeatherEdge[]>({
    queryKey: ["/api/weather/edges"],
    queryFn: async () => {
      const res = await fetch("/api/weather/edges");
      if (!res.ok) throw new Error("Failed to fetch weather edges");
      return res.json();
    },
    refetchInterval: 300000, // 5 minutes (weather models don't update that fast)
  });

  return (
    <div className="reveal-stack flex flex-col gap-6 w-full max-w-7xl mx-auto p-4 md:p-6 h-full">
      <PageHeader
        index="02"
        kicker="MODULE // WEATHER TERMINAL"
        title="Weather Terminal"
        description="Directional edge & meteorological arbitrage"
        icon={CloudRain}
      />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main Arbitrage Grid (2/3 width) */}
        <div className="lg:col-span-2 space-y-6">
          <Card className="overflow-hidden">
            <CardHeader className="border-b bg-muted/20 pb-4">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-xl flex items-center gap-2">
                    <CloudRain className="h-5 w-5 text-primary" strokeWidth={1.5} />
                    Weather Arbitrage Terminal
                  </CardTitle>
                  <CardDescription>
                    Risk-free arbitrage pairs explicitly involving weather events.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <MarketBrowser 
                onlyWeather={true} 
                autoRefresh={true} 
                refreshInterval="1" 
                defaultInvestment={500}
              />
            </CardContent>
          </Card>
        </div>

        {/* Sidebar: Meteorological Edge (1/3 width) */}
        <div className="space-y-6">
          <Card className="scanline relative overflow-hidden">
            <CardHeader className="bg-muted/10 border-b">
              <CardTitle className="text-xl flex items-center gap-2">
                <TrendingUp className="h-5 w-5 text-primary" strokeWidth={1.5} />
                Directional Edge
              </CardTitle>
              <CardDescription>
                Model Probability vs. Market Price (Open-Meteo GFS)
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {loadingEdges ? (
                <div className="p-8 text-center space-y-4">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-500 mx-auto"></div>
                  <p className="text-sm text-muted-foreground font-mono">Running GFS Ensemble Analysis...</p>
                </div>
              ) : edges && edges.length > 0 ? (
                <ScrollArea className="h-[600px]">
                  <div className="divide-y divide-white/5">
                    {edges.map((edge, idx) => (
                      <div key={idx} className="p-4 hover:bg-muted/30 transition-colors group">
                        <div className="flex justify-between items-start mb-2">
                          <Badge variant="outline" className="bg-cyan-500/10 text-cyan-400 border-none flex gap-1 items-center">
                            <Thermometer className="h-3 w-3" /> {edge.analysis.city}
                          </Badge>
                          <div className={`flex items-center font-mono font-bold text-xs ${edge.analysis.edge > 0 ? 'text-emerald-500' : 'text-red-500'}`}>
                            {edge.analysis.edge > 0 ? <ArrowUpRight className="h-3 w-3 mr-1" /> : <ArrowDownRight className="h-3 w-3 mr-1" />}
                            {Math.abs(edge.analysis.edge)}% Edge
                          </div>
                        </div>
                        
                        <p className="text-sm font-medium leading-snug mb-3">
                          {edge.market.title}
                        </p>
                        
                        <div className="space-y-3">
                          <div className="space-y-1">
                            <div className="flex justify-between text-[11px] text-muted-foreground uppercase font-mono">
                              <span>Model Likelihood</span>
                              <span className="text-foreground">{edge.analysis.modelProb}%</span>
                            </div>
                            <Progress value={edge.analysis.modelProb} className="h-1.5 bg-muted/50" />
                          </div>
                          
                          <div className="space-y-1">
                            <div className="flex justify-between text-[11px] text-muted-foreground uppercase font-mono">
                              <span>Market Price (Implied)</span>
                              <span className="text-foreground">{edge.analysis.marketProb}%</span>
                            </div>
                            <Progress value={edge.analysis.marketProb} className="h-1.5 bg-muted/50" />
                          </div>
                        </div>

                        <div className="mt-4 flex gap-2">
                          <Badge className={`w-full justify-center flex py-1 text-[10px] font-bold tracking-widest uppercase ${
                            edge.analysis.recommendation === 'buy_yes' ? 'bg-emerald-600 text-white' : 
                            edge.analysis.recommendation === 'buy_no' ? 'bg-red-600 text-white' : 
                            'bg-muted text-muted-foreground'
                          }`}>
                            {edge.analysis.recommendation.replace('_', ' ')}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              ) : (
                <div className="flex flex-col items-center justify-center p-12 text-center text-muted-foreground">
                  <AlertCircle className="h-10 w-10 mb-4 opacity-20" />
                  <p className="text-sm">No significant mispricings detected in current weather markets.</p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border shadow-sm bg-muted/10 backdrop-blur-sm grayscale opacity-60">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Info className="h-4 w-4 text-muted-foreground" />
                Data Source: GFS 0.25°
              </CardTitle>
            </CardHeader>
            <CardContent className="text-[10px] text-muted-foreground leading-relaxed">
              Model output is interpolated from the Global Forecast System (GFS) 0.25 degree ensemble. 
              Edges are calculated using a 31-member probabilistic spread. Use Kelly Criterion for sizing.
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
