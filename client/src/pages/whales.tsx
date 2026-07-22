import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FishSymbol, Crown, Activity, User, ExternalLink } from "lucide-react";
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

interface PoolMarket {
  id: string;
  title: string;
  volume: number;
  yesPrice: number | null;
  endDate: string | null;
  marketUrl: string | null;
}

interface PoolSection {
  biggest: PoolMarket | null;
  rows: PoolMarket[];
  hasVolume: boolean;
}

interface MarketPools {
  predictit: PoolSection;
  ibkr: PoolSection;
}

const ACCENTS = {
  polymarket: "hsl(var(--chart-4))",
  predictit: "hsl(var(--chart-1))",
  ibkr: "hsl(var(--chart-3))",
};

const formatCurrency = (val: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(val);

function SpotlightCard({
  platform,
  accent,
  loading,
  title,
  subtitle,
  stats,
  emptyNote,
}: {
  platform: string;
  accent: string;
  loading: boolean;
  title: string | null;
  subtitle: string;
  stats: { label: string; value: string }[];
  emptyNote: string;
}) {
  return (
    <Card className="relative overflow-hidden" style={{ borderTop: `2px solid ${accent}` }}>
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.06]"
        style={{ background: `radial-gradient(circle at 20% 0%, ${accent}, transparent 60%)` }}
      />
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] uppercase tracking-[0.25em]" style={{ color: accent }}>
            {platform}
          </span>
          <Crown className="h-4 w-4" style={{ color: accent }} strokeWidth={1.5} />
        </div>
        <CardDescription className="text-[10px] uppercase tracking-widest font-mono">
          Apex Whale
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : title ? (
          <div className="flex flex-col gap-2">
            <span className="font-bold text-sm leading-tight line-clamp-2">{title}</span>
            <span className="text-[10px] text-muted-foreground font-mono">{subtitle}</span>
            <div className="flex gap-4 mt-1">
              {stats.map((s) => (
                <div key={s.label} className="flex flex-col">
                  <span className="text-[9px] uppercase tracking-widest text-muted-foreground">{s.label}</span>
                  <span className="font-mono font-bold text-sm" style={{ color: accent }}>{s.value}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground italic">{emptyNote}</p>
        )}
      </CardContent>
    </Card>
  );
}

function PoolPanel({
  name,
  accent,
  section,
  loading,
  note,
}: {
  name: string;
  accent: string;
  section?: PoolSection;
  loading: boolean;
  note: string;
}) {
  return (
    <Card>
      <CardHeader className="border-b bg-muted/20">
        <CardTitle className="text-lg flex items-center gap-2">
          <span className="h-2 w-2" style={{ backgroundColor: accent }} />
          {name} Money Pools
        </CardTitle>
        <CardDescription className="text-xs">{note}</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : !section || section.rows.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground italic text-xs">
            No cached markets yet — run a scan to populate.
          </div>
        ) : (
          <ScrollArea className="h-[360px]">
            <div className="divide-y divide-white/5">
              {section.rows.map((m, i) => (
                <a
                  key={m.id}
                  href={m.marketUrl ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-start gap-3 p-3 hover:bg-muted/30 transition-colors group"
                >
                  <span className="font-mono text-[10px] font-bold text-muted-foreground mt-0.5 w-4 text-right">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium line-clamp-2 group-hover:text-primary transition-colors">
                      {m.title}
                    </p>
                    <div className="flex gap-3 mt-1 font-mono text-[10px] text-muted-foreground">
                      {section.hasVolume && m.volume > 0 && <span style={{ color: accent }}>{formatCurrency(m.volume)}</span>}
                      {m.yesPrice != null && <span>YES {(m.yesPrice * 100).toFixed(0)}¢</span>}
                    </div>
                  </div>
                  {m.marketUrl && (
                    <ExternalLink className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100 mt-1" />
                  )}
                </a>
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
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

  const { data: pools, isLoading: loadingPools } = useQuery<MarketPools>({
    queryKey: ["/api/whales/market-pools"],
    queryFn: async () => {
      const res = await fetch("/api/whales/market-pools");
      if (!res.ok) throw new Error("Failed to fetch market pools");
      return res.json();
    },
    refetchInterval: 120000,
    retry: 1,
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

  const topTrader = leaderboard?.[0] ?? null;
  const selectedWhaleName =
    leaderboard?.find((w) => w.proxyAddress === selectedWhale)?.username || "whale";

  return (
    <div className="reveal-stack flex flex-col gap-6 w-full max-w-7xl mx-auto p-4 md:p-6 h-full">
      <PageHeader
        index="02"
        kicker="MODULE // WHALE TRACKER"
        title="Whale Tracker"
        description="Biggest players per venue — real traders on Polymarket, money concentration on PredictIt and IBKR"
        icon={FishSymbol}
      />

      {/* Apex whale spotlight — one per market */}
      <div className="grid gap-4 md:grid-cols-3">
        <SpotlightCard
          platform="Polymarket"
          accent={ACCENTS.polymarket}
          loading={loadingLeaderboard}
          title={topTrader ? topTrader.username || "Anonymous" : null}
          subtitle={
            topTrader?.proxyAddress
              ? `${topTrader.proxyAddress.slice(0, 6)}...${topTrader.proxyAddress.slice(-4)}`
              : ""
          }
          stats={
            topTrader
              ? [
                  { label: "PNL", value: formatCurrency(topTrader.pnl) },
                  { label: "Volume", value: formatCurrency(topTrader.volume) },
                ]
              : []
          }
          emptyNote="Leaderboard unavailable."
        />
        <SpotlightCard
          platform="PredictIt"
          accent={ACCENTS.predictit}
          loading={loadingPools}
          title={pools?.predictit.biggest?.title ?? null}
          subtitle="Largest money pool (no public trader identities)"
          stats={
            pools?.predictit.biggest && pools.predictit.hasVolume
              ? [{ label: "Volume", value: formatCurrency(pools.predictit.biggest.volume) }]
              : []
          }
          emptyNote="No cached PredictIt markets yet."
        />
        <SpotlightCard
          platform="IBKR"
          accent={ACCENTS.ibkr}
          loading={loadingPools}
          title={pools?.ibkr.biggest?.title ?? null}
          subtitle="Largest money pool (no public trader identities)"
          stats={
            pools?.ibkr.biggest && pools.ibkr.hasVolume
              ? [{ label: "Volume", value: formatCurrency(pools.ibkr.biggest.volume) }]
              : []
          }
          emptyNote="No cached IBKR markets yet."
        />
      </div>

      {/* Per-market detail panels */}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader className="border-b bg-muted/20">
            <CardTitle className="text-lg flex items-center gap-2">
              <span className="h-2 w-2" style={{ backgroundColor: ACCENTS.polymarket }} />
              Polymarket Top Traders
            </CardTitle>
            <CardDescription className="text-xs">
              Ranked by all-time PNL. Click a trader to see their moves.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {loadingLeaderboard ? (
              <div className="p-4 space-y-2">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : (
              <ScrollArea className="h-[360px]">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-8 text-center">#</TableHead>
                      <TableHead>Trader</TableHead>
                      <TableHead className="text-right">PNL</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {leaderboard?.map((whale) => (
                      <TableRow
                        key={whale.proxyAddress}
                        className={`cursor-pointer transition-colors hover:bg-muted/50 ${
                          selectedWhale === whale.proxyAddress ? "bg-muted shadow-inner" : ""
                        }`}
                        onClick={() =>
                          setSelectedWhale(
                            selectedWhale === whale.proxyAddress ? null : whale.proxyAddress
                          )
                        }
                      >
                        <TableCell className="font-mono text-center font-bold text-muted-foreground text-xs">
                          {whale.rank}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Avatar className="h-6 w-6 border border-white/10">
                              <AvatarImage src={whale.image} />
                              <AvatarFallback>
                                <User className="h-3 w-3" />
                              </AvatarFallback>
                            </Avatar>
                            <span className="font-medium truncate max-w-[110px] text-xs">
                              {whale.username || "Anonymous"}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right font-mono font-bold text-emerald-500 text-xs">
                          {formatCurrency(whale.pnl)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollArea>
            )}
          </CardContent>
        </Card>

        <PoolPanel
          name="PredictIt"
          accent={ACCENTS.predictit}
          section={pools?.predictit}
          loading={loadingPools}
          note="PredictIt keeps traders anonymous — tracking where the money concentrates instead."
        />

        <PoolPanel
          name="IBKR"
          accent={ACCENTS.ibkr}
          section={pools?.ibkr}
          loading={loadingPools}
          note="IBKR event contracts don't expose trader data — tracking the deepest markets instead."
        />
      </div>

      {/* Activity drill-down for selected Polymarket whale */}
      {selectedWhale && (
        <Card className="scanline relative overflow-hidden">
          <CardHeader className="border-b bg-muted/20">
            <CardTitle className="text-lg flex items-center gap-2">
              <Activity className="h-5 w-5" style={{ color: ACCENTS.polymarket }} strokeWidth={1.5} />
              Live Activity — {selectedWhaleName}
            </CardTitle>
            <CardDescription className="text-xs">Most recent trades for this wallet.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {loadingActivity ? (
              <div className="p-4 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {[...Array(6)].map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full" />
                ))}
              </div>
            ) : (
              <ScrollArea className="h-[320px]">
                <div className="grid gap-px md:grid-cols-2 lg:grid-cols-3 bg-white/5">
                  {activity?.map((item) => (
                    <div key={item.id} className="bg-card p-4 hover:bg-muted/30 transition-colors group">
                      <div className="flex justify-between items-start mb-1">
                        <Badge
                          variant={item.side === "BUY" ? "default" : "outline"}
                          className={`text-[10px] uppercase font-bold border-none ${
                            item.side === "BUY"
                              ? "bg-emerald-500/20 text-emerald-400"
                              : "bg-red-500/20 text-red-400"
                          }`}
                        >
                          {item.side}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {new Date(item.timestamp).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
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
                          <span className="text-xs font-mono font-bold text-foreground">
                            {(item.price * 100).toFixed(1)}¢
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                  {activity?.length === 0 && (
                    <div className="p-8 text-center text-muted-foreground italic text-xs md:col-span-2 lg:col-span-3 bg-card">
                      No recent activity found.
                    </div>
                  )}
                </div>
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
