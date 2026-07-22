import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FishSymbol, TrendingUp, Info, DollarSign, Activity, ExternalLink, User } from "lucide-react";
import { PageHeader } from "@/components/terminal";
import { useQuery } from "@tanstack/react-query";
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useState } from "react";

interface Whale {
  username: string;
  proxyAddress: string;
  pnl: number;
  volume: number;
  rank: number;
  image?: string;
}

interface ActivityItem {
  id: string;
  title: string;
  side: string;
  amount: number;
  price: number;
  timestamp: string;
  icon?: string;
}

export default function WhaleTrackerPage() {
  const [selectedWhale, setSelectedWhale] = useState<string | null>(null);

  const { data: leaderboard, isLoading: loadingLeaderboard } = useQuery<Whale[]>({
    queryKey: ["/api/whales/leaderboard"],
    queryFn: async () => {
      const res = await fetch("/api/whales/leaderboard");
      if (!res.ok) throw new Error("Failed to fetch leaderboard");
      return res.json();
    },
    refetchInterval: 60000,
  });

  const { data: activity, isLoading: loadingActivity } = useQuery<ActivityItem[]>({
    queryKey: ["/api/whales/activity", selectedWhale],
    queryFn: async () => {
      if (!selectedWhale) return [];
      const res = await fetch(`/api/whales/activity?address=${selectedWhale}`);
      if (!res.ok) throw new Error("Failed to fetch activity");
      return res.json();
    },
    enabled: !!selectedWhale,
  });

  const formatCurrency = (val: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0
    }).format(val);
  };

  return (
    <div className="reveal-stack flex flex-col gap-6 w-full max-w-7xl mx-auto p-4 md:p-6 h-full">
      <PageHeader
        index="03"
        kicker="MODULE // WHALE TRACKER"
        title="Whale Tracker"
        description="Positioning of the most profitable traders on Polymarket"
        icon={FishSymbol}
      />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Leaderboard Section */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-xl flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-chart-1" strokeWidth={1.5} />
              Top Traders (PNL)
            </CardTitle>
            <CardDescription>
              Ranked by all-time profit on Polymarket.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loadingLeaderboard ? (
              <div className="space-y-2">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12 text-center">#</TableHead>
                    <TableHead>Trader</TableHead>
                    <TableHead className="text-right">PNL</TableHead>
                    <TableHead className="text-right">Volume</TableHead>
                    <TableHead className="w-10"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {leaderboard?.map((whale) => (
                    <TableRow 
                      key={whale.proxyAddress} 
                      className={`cursor-pointer transition-colors hover:bg-muted/50 ${selectedWhale === whale.proxyAddress ? 'bg-muted shadow-inner' : ''}`}
                      onClick={() => setSelectedWhale(whale.proxyAddress)}
                    >
                      <TableCell className="font-mono text-center font-bold text-muted-foreground">
                        {whale.rank}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Avatar className="h-8 w-8 border border-white/10">
                            <AvatarImage src={whale.image} />
                            <AvatarFallback><User className="h-4 w-4" /></AvatarFallback>
                          </Avatar>
                          <div className="flex flex-col">
                            <span className="font-medium truncate max-w-[150px]">{whale.username || "Anonymous"}</span>
                            <span className="text-[10px] text-muted-foreground font-mono">
                              {whale.proxyAddress ? `${whale.proxyAddress.slice(0, 6)}...${whale.proxyAddress.slice(-4)}` : "No Address"}
                            </span>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono font-bold text-emerald-500">
                        {formatCurrency(whale.pnl)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-muted-foreground font-medium">
                        {formatCurrency(whale.volume)}
                      </TableCell>
                      <TableCell>
                        <ExternalLink className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100" />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Activity Section */}
        <Card className="scanline relative overflow-hidden">
          <CardHeader className="border-b bg-muted/20">
            <CardTitle className="text-xl flex items-center gap-2">
              <Activity className="h-5 w-5 text-primary" strokeWidth={1.5} />
              Live Activity
            </CardTitle>
            <CardDescription>
              {selectedWhale ? "Recent trades for selected whale" : "Select a trader to view activity"}
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {!selectedWhale ? (
              <div className="flex flex-col items-center justify-center p-12 text-center text-muted-foreground">
                <FishSymbol className="h-10 w-10 mb-4 opacity-20" />
                <p className="text-sm">Click a trader from the leaderboard to see their latest moves.</p>
              </div>
            ) : loadingActivity ? (
              <div className="p-4 space-y-4">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : (
              <ScrollArea className="h-[500px]">
                <div className="divide-y divide-white/5">
                  {activity?.map((item) => (
                    <div key={item.id} className="p-4 hover:bg-muted/30 transition-colors group">
                      <div className="flex justify-between items-start mb-1">
                        <Badge variant={item.side === "BUY" ? "default" : "outline"} className={`text-[10px] uppercase font-bold ${item.side === "BUY" ? "bg-emerald-500/20 text-emerald-400 border-none" : "bg-red-500/20 text-red-400 border-none"}`}>
                          {item.side}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <p className="text-xs font-medium line-clamp-2 mb-2 group-hover:text-primary transition-colors">
                        {item.title}
                      </p>
                      <div className="flex justify-between items-end">
                        <div className="flex flex-col">
                          <span className="text-[10px] text-muted-foreground uppercase tracking-tight">Amount</span>
                          <span className="text-xs font-mono font-bold">{formatCurrency(item.amount)}</span>
                        </div>
                        <div className="text-right">
                          <span className="text-[10px] text-muted-foreground uppercase tracking-tight block">Price</span>
                          <span className="text-xs font-mono font-bold text-foreground">{(item.price * 100).toFixed(1)}¢</span>
                        </div>
                      </div>
                    </div>
                  ))}
                  {activity?.length === 0 && (
                    <div className="p-8 text-center text-muted-foreground italic text-xs">
                      No recent activity found.
                    </div>
                  )}
                </div>
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
