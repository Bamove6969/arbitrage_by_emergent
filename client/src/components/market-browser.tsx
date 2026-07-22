import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Search, TrendingUp, Loader2, RefreshCw, DollarSign, Plus, Clock, Link2, Check, ArrowUpDown, Filter, Flame, Timer, BarChart3, Zap, Star, ThumbsUp, ThumbsDown, HelpCircle, ExternalLink, PanelLeft, PanelRight, Layers, EyeOff, Terminal } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { useToast } from "@/hooks/use-toast";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Slider } from "@/components/ui/slider";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useComparison } from "@/contexts/comparison-context";
import { BarChart, Bar, LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, PieChart, Pie, Legend, ScatterChart, Scatter } from "recharts";

interface MarketBrowserProps {
  autoRefresh?: boolean;
  refreshInterval?: string;
  enabledPlatforms?: string[];
  onScanComplete?: () => void;
  excludeWeather?: boolean;
  onlyWeather?: boolean;
  defaultInvestment?: number;
}


interface MarketOutcome {
  label: string;
  yesPrice: number;
  noPrice: number;
  volume?: number;
}

interface StandardizedMarket {
  id: string;
  platform: "Kalshi" | "Polymarket" | "PredictIt";
  title: string;
  category?: string;
  yesPrice: number;
  noPrice: number;
  volume: number;
  lastUpdated: string;
  endDate?: string;
  marketUrl?: string;
  isBinary: boolean;
  outcomeCount: number;
  contractLabel?: string;
  outcomes?: MarketOutcome[];
  isWeather?: number;
}

// Detect if user is on mobile device (touch-primary OR mobile UA)
function isMobileDevice(): boolean {
  if (typeof window === 'undefined') return false;
  
  // Check for touch-primary device (includes fold phones, tablets)
  const hasCoarsePointer = window.matchMedia('(pointer: coarse)').matches;
  // Check for mobile user agent - includes fold phones with large screens
  const isMobileUA = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  
  // Fold phones have touch AND mobile UA, even with large screens
  return hasCoarsePointer || isMobileUA;
}

// Open both market URLs - split screen on desktop, two tabs on mobile
function openBothMarkets(urlA: string, urlB: string, toast: any): void {
  if (!urlA || !urlB) {
    toast({
      title: "Missing URLs",
      description: "One or both markets don't have viewable URLs",
      variant: "destructive",
    });
    return;
  }
  
  const mobile = isMobileDevice();
  
  if (mobile) {
    // Mobile: Open two tabs sequentially
    const windowA = window.open(urlA, '_blank');
    
    // Small delay to avoid popup blockers
    setTimeout(() => {
      const windowB = window.open(urlB, '_blank');
      
      if (!windowA || !windowB) {
        // Try to copy to clipboard, but handle errors gracefully
        try {
          navigator.clipboard?.writeText(`${urlA}\n${urlB}`).catch(() => {});
        } catch (e) {
          // Clipboard access denied - that's okay
        }
        toast({
          title: "Pop-up blocked",
          description: `Please allow pop-ups, or manually open:\n${urlA}\n${urlB}`,
        });
      }
    }, 100);
  } else {
    // Desktop: Try to open side-by-side windows
    const screenWidth = window.screen.availWidth;
    const screenHeight = window.screen.availHeight;
    const halfWidth = Math.floor(screenWidth / 2);
    
    // Left window
    const leftFeatures = `width=${halfWidth},height=${screenHeight},left=0,top=0,menubar=no,toolbar=yes,location=yes,status=yes,resizable=yes,scrollbars=yes`;
    const windowA = window.open(urlA, 'marketA', leftFeatures);
    
    // Right window
    const rightFeatures = `width=${halfWidth},height=${screenHeight},left=${halfWidth},top=0,menubar=no,toolbar=yes,location=yes,status=yes,resizable=yes,scrollbars=yes`;
    const windowB = window.open(urlB, 'marketB', rightFeatures);
    
    if (!windowA || !windowB) {
      toast({
        title: "Pop-up blocked",
        description: "Please allow pop-ups for side-by-side view. Opening in tabs instead...",
      });
      // Fallback to regular tabs
      window.open(urlA, '_blank');
      setTimeout(() => window.open(urlB, '_blank'), 100);
    } else {
      toast({
        title: "Markets opened",
        description: "Both markets opened side-by-side. You may need to arrange windows manually.",
      });
    }
  }
}

// Calculate profit based on investment amount and ROI
function calculateProfit(roi: number, investment: number): { profit: number; payout: number; contracts: number } {
  if (roi <= 0 || investment <= 0) return { profit: 0, payout: 0, contracts: 0 };
  const profit = (investment * roi) / 100;
  const payout = investment + profit;
  const contracts = Math.floor(investment); // Simplified: 1 contract = $1
  return { profit, payout, contracts };
}

interface ComboLeg {
  platform: string;
  marketId: string;
  title: string;
  side: "YES" | "NO";
  price: number;
  fee: number;
  volume: number;
  marketUrl?: string | null;
  allocation: number;
}

interface ArbitrageOpportunity {
  marketA: StandardizedMarket;
  marketB: StandardizedMarket;
  combinedYesCost: number;
  totalCost?: number;
  fees?: number;
  potentialProfit: number;
  roi: number;
  matchScore: number;
  matchReason: string;
  comboType?: "pair";
  legCount?: number;
  legs?: ComboLeg[];
  earliestResolution?: string | null;
  scenario?: number | string;
  isVerified?: boolean;
}

// Extended opportunity with computed fields for sorting
interface EnrichedOpportunity extends ArbitrageOpportunity {
  opportunityScore: number;
  liquidityScore: number;
  daysToExpiry: number | null;
  roiHistory?: number[];
}

type SortOption = "roi" | "score" | "ending-soon" | "ending-late" | "liquidity" | "hot";

const SORT_OPTIONS: { value: SortOption; label: string; icon: any }[] = [
  { value: "roi", label: "Highest ROI", icon: TrendingUp },
  { value: "score", label: "Opportunity Score", icon: Star },
  { value: "hot", label: "Hot (ROI + Soon)", icon: Flame },
  { value: "ending-soon", label: "Ending Soonest", icon: Timer },
  { value: "ending-late", label: "Ending Latest", icon: Clock },
  { value: "liquidity", label: "Highest Liquidity", icon: BarChart3 },
];

const QUICK_PRESETS = [
  { id: "best-roi", label: "Best ROI", sort: "roi" as SortOption, minRoi: 3 },
  { id: "hot-opportunities", label: "Hot Opportunities", sort: "hot" as SortOption, minRoi: 1 },
  { id: "quick-wins", label: "Quick Wins", sort: "ending-soon" as SortOption, minRoi: 2 },
  { id: "safe-bets", label: "High Volume", sort: "liquidity" as SortOption, minRoi: 1 },
];

interface MarketStats {
  kalshi: number;
  polymarket: number;
  predictit: number;
  ibkr: number;
  total: number;
  lastUpdated: string;
}

const platformColors: Record<string, string> = {
  Kalshi: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  Polymarket: "bg-purple-500/10 text-purple-700 dark:text-purple-400",
  PredictIt: "bg-green-500/10 text-green-700 dark:text-green-400",
  IBKR: "bg-red-500/10 text-red-700 dark:text-red-400",
};

const platformAccent: Record<string, { border: string; bg: string; text: string; ring: string }> = {
  Kalshi: { border: "border-l-blue-500", bg: "bg-blue-500/5", text: "text-blue-700 dark:text-blue-400", ring: "ring-blue-500/20" },
  Polymarket: { border: "border-l-purple-500", bg: "bg-purple-500/5", text: "text-purple-700 dark:text-purple-400", ring: "ring-purple-500/20" },
  PredictIt: { border: "border-l-green-500", bg: "bg-green-500/5", text: "text-green-700 dark:text-green-400", ring: "ring-green-500/20" },
  IBKR: { border: "border-l-red-500", bg: "bg-red-500/5", text: "text-red-700 dark:text-red-400", ring: "ring-red-500/20" },
};

function formatExpiry(dateStr?: string | null): string | null {
  if (!dateStr) return null;
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
  } catch { return null; }
}

export function BackendLogViewer() {
  const [logs, setLogs] = useState<string[]>([]);
  const scrollRef = { current: null as HTMLDivElement | null };
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    let eventSource: EventSource | null = null;
    if (isOpen) {
      eventSource = new EventSource("/api/logs");
      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.logs && Array.isArray(data.logs)) {
            setLogs(data.logs);
          }
        } catch (err) {
          console.error("Failed to parse logs", err);
        }
      };
    }
    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [isOpen]);

  useEffect(() => {
    const el = document.getElementById('log-scroll-area');
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-[44px] gap-2 ml-auto shadow-sm" data-testid="button-live-logs">
          <Terminal className="w-4 h-4 text-emerald-500" />
          Live Logs
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden border-zinc-800 bg-zinc-950">
        <DialogHeader className="px-5 py-4 border-b border-zinc-900 bg-zinc-950/50">
          <DialogTitle className="flex items-center gap-2 text-zinc-100 font-mono text-sm tracking-tight">
            <Terminal className="w-4 h-4 text-emerald-500" />
            Backend Terminal Stream
            <Badge variant="outline" className="ml-2 font-mono text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/20">CONNECTED</Badge>
          </DialogTitle>
        </DialogHeader>
        <div 
          id="log-scroll-area"
          className="flex-1 bg-zinc-950 text-emerald-400 font-mono text-xs p-5 overflow-y-auto overflow-x-hidden selection:bg-emerald-900/50 leading-relaxed"
        >
          <div className="space-y-1 block">
            {logs.length === 0 ? (
              <div className="text-zinc-600 italic">Waiting for python standard output...</div>
            ) : (
              logs.map((log, idx) => (
                <div key={idx} className="break-all whitespace-pre-wrap opacity-90 hover:opacity-100 hover:bg-zinc-900/30 px-1 -mx-1 rounded transition-colors">
                  {log}
                </div>
              ))
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function LegSection({ leg, market, oppIdx, legIdx }: {
  leg: ComboLeg;
  market: StandardizedMarket;
  oppIdx: number;
  legIdx: number;
}) {
  const accent = platformAccent[leg.platform] || platformAccent.Kalshi;
  const yesPercent = market.yesPrice * 100;
  const noPercent = market.noPrice * 100;
  const buyPrice = leg.price * 100;
  const expiry = formatExpiry(market.endDate);

  return (
    <div className={`border-l-4 ${accent.border} ${accent.bg} rounded-r-lg p-3 sm:p-4 space-y-2.5`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className={`text-sm font-bold uppercase tracking-wide ${accent.text}`} data-testid={`text-platform-${oppIdx}-${legIdx}`}>
          {leg.platform}
        </span>
        <Badge variant="secondary" className="font-mono text-sm font-bold px-2.5 py-1 shrink-0">
          Buy {leg.side} {buyPrice.toFixed(0)}¢
        </Badge>
      </div>

      <div className="text-[15px] sm:text-base leading-relaxed font-medium text-foreground break-words" data-testid={`text-question-${oppIdx}-${legIdx}`}>
        {leg.marketUrl ? (
          <a
            href={leg.marketUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:underline hover:text-blue-600 dark:hover:text-blue-400 decoration-1 underline-offset-4 group inline-flex items-center gap-1.5"
          >
            {leg.title}
          </a>
        ) : (
          <p>{leg.title}</p>
        )}
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-1 rounded-md bg-green-500/10 px-2 py-0.5">
            <span className="text-xs font-medium text-green-700 dark:text-green-400">Yes</span>
            <span className="text-sm font-bold font-mono text-green-700 dark:text-green-400">{yesPercent.toFixed(0)}¢</span>
          </div>
          <div className="flex items-center gap-1 rounded-md bg-red-500/10 px-2 py-0.5">
            <span className="text-xs font-medium text-red-700 dark:text-red-400">No</span>
            <span className="text-sm font-bold font-mono text-red-700 dark:text-red-400">{noPercent.toFixed(0)}¢</span>
          </div>
        </div>
        {leg.fee > 0 && (
          <span className="text-xs text-amber-600 dark:text-amber-400 font-mono" data-testid={`text-fee-${oppIdx}-${legIdx}`}>
            fee {(leg.fee * 100).toFixed(1)}¢
          </span>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap text-xs text-muted-foreground">
        <div className="flex items-center gap-3 flex-wrap">
          {leg.volume > 0 && (
            <span className="flex items-center gap-1 font-mono">
              <BarChart3 className="w-3 h-3" />
              {leg.volume.toLocaleString()}
            </span>
          )}
          {expiry && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {expiry}
            </span>
          )}
        </div>
        {leg.marketUrl && (
          <a
            href={leg.marketUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 underline underline-offset-2 decoration-blue-600/40 hover:decoration-blue-600 hover:text-blue-700 dark:hover:text-blue-300 font-medium whitespace-nowrap"
            data-testid={`link-leg-${oppIdx}-${legIdx}`}
          >
            {leg.platform}
            <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>
    </div>
  );
}

function AllMarketsGrid({ markets, platformColors }: { markets: StandardizedMarket[]; platformColors: Record<string, string> }) {
  const { pinLeft, pinRight, leftMarket, rightMarket } = useComparison();
  const { toast } = useToast();

  const handlePinLeft = (market: StandardizedMarket) => {
    pinLeft({
      id: market.id,
      platform: market.platform,
      title: market.title,
      yesPrice: market.yesPrice,
      noPrice: market.noPrice,
      marketUrl: market.marketUrl,
      outcomes: market.outcomes,
      outcomeCount: market.outcomeCount,
    });
    toast({
      title: "Pinned to left",
      description: `${market.platform}: ${market.title.slice(0, 50)}...`,
    });
  };

  const handlePinRight = (market: StandardizedMarket) => {
    pinRight({
      id: market.id,
      platform: market.platform,
      title: market.title,
      yesPrice: market.yesPrice,
      noPrice: market.noPrice,
      marketUrl: market.marketUrl,
      outcomes: market.outcomes,
      outcomeCount: market.outcomeCount,
    });
    toast({
      title: "Pinned to right",
      description: `${market.platform}: ${market.title.slice(0, 50)}...`,
    });
  };

  return (
    <ScrollArea className="h-[400px]">
      <div className="space-y-2 pr-4">
        {markets.map((market) => (
          <div 
            key={market.id} 
            className="p-3 rounded-md border hover-elevate space-y-2"
            data-testid={`market-${market.id}`}
          >
            <div className="flex items-start justify-between gap-2">
              <p className="font-medium text-sm leading-snug flex-1">{market.title}</p>
              {market.outcomeCount > 2 && (
                <Badge variant="secondary" className="text-xs shrink-0">
                  <Layers className="w-3 h-3 mr-1" />
                  {market.outcomeCount} options
                </Badge>
              )}
            </div>
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className={platformColors[market.platform]}>
                  {market.platform}
                </Badge>
                {market.category && (
                  <Badge variant="outline" className="text-xs">
                    {market.category}
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-3 font-mono text-sm">
                <span>
                  YES: <span className="font-bold text-green-600 dark:text-green-400">{(market.yesPrice * 100).toFixed(0)}c</span>
                </span>
                <span className="text-muted-foreground">
                  NO: <span className="font-bold">{(market.noPrice * 100).toFixed(0)}c</span>
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                variant={leftMarket?.id === market.id ? "default" : "outline"}
                className="min-h-[36px] text-xs"
                onClick={() => handlePinLeft(market)}
                data-testid={`button-pin-left-${market.id}`}
              >
                <PanelLeft className="w-3 h-3 mr-1" />
                Compare Left
              </Button>
              <Button
                size="sm"
                variant={rightMarket?.id === market.id ? "default" : "outline"}
                className="min-h-[36px] text-xs"
                onClick={() => handlePinRight(market)}
                data-testid={`button-pin-right-${market.id}`}
              >
                <PanelRight className="w-3 h-3 mr-1" />
                Compare Right
              </Button>
              {market.marketUrl && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="min-h-[36px] text-xs ml-auto"
                  onClick={() => window.open(market.marketUrl, '_blank')}
                  data-testid={`button-open-${market.id}`}
                >
                  <ExternalLink className="w-3 h-3 mr-1" />
                  Open
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

export function MarketBrowser({ 
  autoRefresh = false, 
  refreshInterval = "5", 
  enabledPlatforms = ["Kalshi", "Polymarket", "PredictIt", "IBKR Forecast"],
  onScanComplete,
  excludeWeather = false,
  onlyWeather = false,
  defaultInvestment = 100
}: MarketBrowserProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState("opportunities");
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [scanStartTime, setScanStartTime] = useState<Date | null>(null);
  const [scanElapsed, setScanElapsed] = useState(0);
  const [scanProgress, setScanProgress] = useState<{
    percent: number;
    phase: string;
    message: string;
    status: string;
    totalComparisons: number;
    completedComparisons: number;
    pairsFound: number;
    totalMarkets: number;
  } | null>(null);
  const [manualPairOpen, setManualPairOpen] = useState(false);
  const [marketA, setMarketA] = useState<StandardizedMarket | null>(null);
  const [marketB, setMarketB] = useState<StandardizedMarket | null>(null);
  const [pairSearchA, setPairSearchA] = useState("");
  const [pairSearchB, setPairSearchB] = useState("");
  
  // Investment amount for profit calculations
  const [investmentAmount, setInvestmentAmount] = useState(defaultInvestment.toString());
  
  // Sorting and filtering state
  const [sortBy, setSortBy] = useState<SortOption>("roi");
  const [minRoiFilter, setMinRoiFilter] = useState(0);
  const [showFilters, setShowFilters] = useState(false);
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedHistoryOpp, setSelectedHistoryOpp] = useState<EnrichedOpportunity | null>(null);
  
  const [ratedPairs, setRatedPairs] = useState<Map<string, string>>(new Map());
  const [dismissedPairs, setDismissedPairs] = useState<Set<string>>(new Set());
  
  const { toast } = useToast();
  
  // Feedback mutation for machine learning
  const feedbackMutation = useMutation({
    mutationFn: async ({ opportunity, verdict }: { opportunity: ArbitrageOpportunity; verdict: 'approve' | 'reject' | 'not_binary' | 'not_interested' }) => {
      return await apiRequest('POST', '/api/match-feedback', {
        marketAId: opportunity.marketA.id,
        marketATitle: opportunity.marketA.title,
        marketAPlatform: opportunity.marketA.platform,
        marketBId: opportunity.marketB.id,
        marketBTitle: opportunity.marketB.title,
        marketBPlatform: opportunity.marketB.platform,
        matchScore: opportunity.matchScore,
        matchReason: opportunity.matchReason,
        verdict,
      });
    },
    onSuccess: (_, variables) => {
      const pairKey = `${variables.opportunity.marketA.id}-${variables.opportunity.marketB.id}`;
      if (variables.verdict === 'not_interested') {
        setDismissedPairs(prev => new Set(Array.from(prev).concat([pairKey])));
        toast({
          title: "Dismissed",
          description: "This opportunity has been hidden.",
        });
      } else {
        setRatedPairs(prev => new Map(Array.from(prev).concat([[pairKey, variables.verdict]])));
        const verdictLabel = variables.verdict === 'approve' ? 'Good match' : variables.verdict === 'reject' ? 'Not a match' : 'Not binary';
        toast({
          title: "Feedback recorded",
          description: `Marked as: ${verdictLabel}. Thanks for improving our matching!`,
        });
      }
    },
    onError: (error: any) => {
      if (error.message?.includes('409')) {
        toast({
          title: "Already rated",
          description: "You've already provided feedback for this pair.",
        });
      } else {
        toast({
          title: "Error",
          description: "Failed to save feedback. Please try again.",
          variant: "destructive",
        });
      }
    },
  });

  useEffect(() => {
    const checkStatus = () => {
      fetch("/api/scan-status")
        .then(res => res.json())
        .then(state => {
          if (state.is_scanning) {
            setIsScanning(true);
            setScanStartTime(new Date());
            setScanElapsed(0);
          }
        })
        .catch(() => {});
    };

    checkStatus();
    
    if (autoRefresh) {
      const timer = setTimeout(checkStatus, 1000);
      return () => clearTimeout(timer);
    }
  }, [autoRefresh]);

  useEffect(() => {
    fetch("/api/match-feedback")
      .then(res => res.json())
      .then((items: Array<{ marketAId: string; marketBId: string; verdict: string }>) => {
        const rated = new Map<string, string>();
        const dismissed = new Set<string>();
        for (const item of items) {
          const key = `${item.marketAId}-${item.marketBId}`;
          if (item.verdict === 'not_interested') {
            dismissed.add(key);
          } else {
            rated.set(key, item.verdict);
          }
        }
        setRatedPairs(rated);
        setDismissedPairs(dismissed);
      })
      .catch(() => {});
  }, []);

  // Calculate manual pair ROI preview
  const manualPairPreview = useMemo(() => {
    if (!marketA || !marketB) return null;
    // Scenario 1: Buy YES on A + Buy NO on B
    const cost1 = marketA.yesPrice + (1 - marketB.yesPrice);
    const roi1 = cost1 < 1 ? ((1 - cost1) / cost1) * 100 : 0;
    // Scenario 2: Buy NO on A + Buy YES on B
    const cost2 = (1 - marketA.yesPrice) + marketB.yesPrice;
    const roi2 = cost2 < 1 ? ((1 - cost2) / cost2) * 100 : 0;
    return { roi1, roi2, cost1, cost2, bestRoi: Math.max(roi1, roi2) };
  }, [marketA, marketB]);

  // Fetch market stats
  const { data: stats } = useQuery<MarketStats>({
    queryKey: ["/api/market-stats"],
    queryFn: async () => {
      const res = await fetch("/api/market-stats");
      if (!res.ok) throw new Error("Failed to fetch stats");
      return res.json();
    },
    staleTime: 60000,
  });

  const { data: rawMarkets = [], isLoading: isMarketsLoading, refetch: refetchMarkets } = useQuery<StandardizedMarket[]>({
    queryKey: ["/api/markets", searchQuery],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (searchQuery) params.set("q", searchQuery);
      const res = await fetch(`/api/markets?${params}`);
      if (!res.ok) throw new Error("Failed to fetch");
      return res.json();
    },
    enabled: activeTab === "all" || manualPairOpen || onlyWeather,
    staleTime: 30000,
  });

  const { data: rawOpps = [], isLoading: isOppsLoading, refetch: refetchOpps } = useQuery<ArbitrageOpportunity[]>({
    queryKey: ["/api/arbitrage-opportunities", searchQuery, enabledPlatforms],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (searchQuery) params.set("q", searchQuery);
      params.set("minRoi", "0");
      params.set("limit", "300");
      params.set("platforms", enabledPlatforms.join(","));
      const res = await fetch(`/api/arbitrage-opportunities?${params}`);
      if (!res.ok) throw new Error("Failed to fetch");
      setLastRefresh(new Date());
      return res.json();
    },
    enabled: activeTab === "opportunities" || onlyWeather,
    staleTime: 5000,
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
  });

  const opportunities = useMemo(() => {
    let filtered = rawOpps;
    if (excludeWeather) {
      filtered = filtered.filter((o: ArbitrageOpportunity) => o.marketA.isWeather !== 1 && o.marketB.isWeather !== 1);
    } else if (onlyWeather) {
      filtered = filtered.filter((o: ArbitrageOpportunity) => o.marketA.isWeather === 1 || o.marketB.isWeather === 1);
    }
    return filtered;
  }, [rawOpps, excludeWeather, onlyWeather]);

  const markets = useMemo(() => {
    let filtered = rawMarkets;
    if (excludeWeather) {
      filtered = filtered.filter((m: StandardizedMarket) => m.isWeather !== 1);
    } else if (onlyWeather) {
      filtered = filtered.filter((m: StandardizedMarket) => m.isWeather === 1);
    }
    return filtered;
  }, [rawMarkets, excludeWeather, onlyWeather]);

  const triggerScan = async () => {
    if (isScanning) return;
    setIsScanning(true);
    setScanStartTime(new Date());
    setScanElapsed(0);
    setScanProgress(null);
    // Immediately show existing results (Colab results stay visible while scan refreshes prices)
    refetchOpps();
    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platforms: enabledPlatforms }),
      });
      const data = await res.json();
      if (data.status === "already_scanning") {
        setIsScanning(false);
        toast({ title: "Scan in progress", description: "A scan is already running. Please wait for it to complete." });
      }
    } catch (e) {
      setIsScanning(false);
      setScanStartTime(null);
      toast({ title: "Scan failed", description: "Could not start scan. Please try again.", variant: "destructive" });
    }
  };

  // Track elapsed time during scanning
  useEffect(() => {
    if (!isScanning || !scanStartTime) return;
    const timer = setInterval(() => {
      setScanElapsed(Math.floor((Date.now() - scanStartTime.getTime()) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [isScanning, scanStartTime]);

  // Subscribe to SSE for real-time progress updates
  useEffect(() => {
    if (!isScanning) return;
    
    const eventSource = new EventSource('/api/scan-progress');
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setScanProgress({
          percent: data.percent || 0,
          phase: data.phase || 'Starting...',
          message: data.message || '',
          status: data.status || 'idle',
          totalComparisons: data.totalComparisons || 0,
          completedComparisons: data.completedComparisons || 0,
          pairsFound: data.pairsFound || 0,
          totalMarkets: data.totalMarkets || 0,
        });
        
        if (data.status === 'complete') {
          eventSource.close();
          setTimeout(() => {
            // Force-invalidate so staleTime doesn't block the update
            queryClient.invalidateQueries({ queryKey: ["/api/arbitrage-opportunities"] });
            queryClient.invalidateQueries({ queryKey: ["/api/market-stats"] });
            queryClient.invalidateQueries({ queryKey: ["/api/markets"] });
            refetchOpps();
            onScanComplete?.();
            setIsScanning(false);
            setScanStartTime(null);
          }, 1000);
        } else if (data.status === 'error') {
          eventSource.close();
          setIsScanning(false);
          setScanStartTime(null);
        }
      } catch (e) {
        // Ignore parse errors
      }
    };
    
    eventSource.onerror = () => {
      eventSource.close();
      setTimeout(() => {
        fetch("/api/scan-status")
          .then(r => r.json())
          .then(s => {
            if (!s.is_scanning) {
              setIsScanning(false);
              setScanStartTime(null);
              refetchOpps();
              queryClient.invalidateQueries({ queryKey: ["/api/market-stats"] });
            }
          })
          .catch(() => {
            setIsScanning(false);
            setScanStartTime(null);
          });
      }, 2000);
    };
    
    return () => {
      eventSource.close();
    };
  }, [isScanning]);

  // Calculate opportunity score (0-100) based on ROI, time, volume, and match confidence
  const calculateOpportunityScore = (opp: ArbitrageOpportunity): number => {
    // ROI component (0-40 points) - scale so 5% ROI = max 40 points
    const roiScore = Math.min(40, (opp.roi / 5) * 40);
    // Confidence component (0-20 points)
    const confidenceScore = Math.min(20, (opp.matchScore / 100) * 20);
    // Volume component (0-20 points) - log scale, capped
    const avgVolume = (opp.marketA.volume + opp.marketB.volume) / 2;
    const volumeScore = Math.min(20, Math.log10(Math.max(1, avgVolume)) * 5);
    // Time component (0-20 points)
    let timeScore = 10;
    const endDate = opp.marketA.endDate || opp.marketB.endDate;
    if (endDate) {
      const daysLeft = (new Date(endDate).getTime() - Date.now()) / (1000 * 60 * 60 * 24);
      if (daysLeft <= 7) timeScore = 20;
      else if (daysLeft <= 30) timeScore = 15;
      else if (daysLeft <= 90) timeScore = 10;
      else timeScore = 5;
    }
    // Clamp total to 0-100
    return Math.min(100, Math.max(0, Math.round(roiScore + confidenceScore + volumeScore + timeScore)));
  };

  // Generate stable ROI history for sparkline using seeded pseudo-random
  const generateStableRoiHistory = (opp: ArbitrageOpportunity): number[] => {
    // Use market IDs as seed for deterministic values
    const seed = (opp.marketA.id + opp.marketB.id).split('').reduce((a, c) => a + c.charCodeAt(0), 0);
    const seededRandom = (i: number) => {
      const x = Math.sin(seed + i) * 10000;
      return x - Math.floor(x);
    };
    const currentRoi = opp.roi;
    const history: number[] = [];
    let value = currentRoi * (0.7 + seededRandom(0) * 0.3);
    for (let i = 0; i < 6; i++) {
      history.push(Math.max(0, value));
      value += (currentRoi - value) * 0.3 + (seededRandom(i + 1) - 0.5) * 0.5;
    }
    history.push(currentRoi);
    return history;
  };

  // Enrich opportunities with computed fields
  const enrichedOpportunities = useMemo((): EnrichedOpportunity[] => {
    return opportunities.map(opp => {
      const endDate = opp.marketA.endDate || opp.marketB.endDate;
      let daysToExpiry: number | null = null;
      if (endDate) {
        daysToExpiry = Math.ceil((new Date(endDate).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
      }
      const avgVolume = (opp.marketA.volume + opp.marketB.volume) / 2;
      const liquidityScore = Math.min(100, Math.log10(avgVolume + 1) * 25);
      return {
        ...opp,
        opportunityScore: calculateOpportunityScore(opp),
        liquidityScore,
        daysToExpiry,
        roiHistory: generateStableRoiHistory(opp),
      };
    });
  }, [opportunities]);

  // Sort and filter opportunities
  const sortedOpportunities = useMemo(() => {
    let filtered = enrichedOpportunities
      .filter(opp => !dismissedPairs.has(`${opp.marketA.id}-${opp.marketB.id}`))
      .filter(opp => opp.roi >= minRoiFilter)
      // Remove expired markets (ended today or earlier)
      .filter(opp => opp.daysToExpiry == null || opp.daysToExpiry >= 0);
    if (selectedCategories.length > 0) {
      filtered = filtered.filter(opp => 
        selectedCategories.includes(opp.marketA.category || "") ||
        selectedCategories.includes(opp.marketB.category || "")
      );
    }
    return [...filtered].sort((a, b) => {
      // ALWAYS prioritize verified matches at the top
      if (a.isVerified && !b.isVerified) return -1;
      if (!a.isVerified && b.isVerified) return 1;

      switch (sortBy) {
        case "roi": return b.roi - a.roi;
        case "score": return b.opportunityScore - a.opportunityScore;
        case "hot":
          const hotScoreA = a.roi * (a.daysToExpiry ? Math.max(1, 30 - a.daysToExpiry) / 30 : 0.5);
          const hotScoreB = b.roi * (b.daysToExpiry ? Math.max(1, 30 - b.daysToExpiry) / 30 : 0.5);
          return hotScoreB - hotScoreA;
        case "ending-soon":
          if (a.daysToExpiry === null && b.daysToExpiry === null) return 0;
          if (a.daysToExpiry === null) return 1;
          if (b.daysToExpiry === null) return -1;
          return a.daysToExpiry - b.daysToExpiry;
        case "ending-late":
          if (a.daysToExpiry === null && b.daysToExpiry === null) return 0;
          if (a.daysToExpiry === null) return 1;
          if (b.daysToExpiry === null) return -1;
          return b.daysToExpiry - a.daysToExpiry;
        case "liquidity": return b.liquidityScore - a.liquidityScore;
        default: return b.roi - a.roi;
      }
    });
  }, [enrichedOpportunities, sortBy, minRoiFilter, selectedCategories, dismissedPairs]);

  useEffect(() => {
    setCurrentPage(1);
  }, [sortBy, minRoiFilter, searchQuery, selectedCategories, activeTab]);

  const PAGE_SIZE = 25;
  const totalPages = Math.min(4, Math.ceil(sortedOpportunities.length / PAGE_SIZE));
  const paginatedOpportunities = sortedOpportunities.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // Apply preset
  const applyPreset = (presetId: string) => {
    const preset = QUICK_PRESETS.find(p => p.id === presetId);
    if (preset) {
      setSortBy(preset.sort);
      setMinRoiFilter(preset.minRoi);
      setActivePreset(presetId);
    }
  };

  // Get unique categories from opportunities
  const availableCategories = useMemo(() => {
    const cats = new Set<string>();
    opportunities.forEach(opp => {
      if (opp.marketA.category) cats.add(opp.marketA.category);
      if (opp.marketB.category) cats.add(opp.marketB.category);
    });
    return Array.from(cats);
  }, [opportunities]);

  const addToWatchlistMutation = useMutation({
    mutationFn: async (opp: ArbitrageOpportunity) => {
      // Store raw YES prices from both markets
      // The watchlist ROI calculator evaluates both scenarios:
      // 1) Buy YES on A + Buy NO on B (uses siteAYesPrice + (1 - siteBYesPrice))
      // 2) Buy NO on A + Buy YES on B (uses (1 - siteAYesPrice) + siteBYesPrice)
      // This matches how the opportunity was detected
      return apiRequest("POST", "/api/watchlist", {
        marketName: `${opp.matchReason || "arbitrage"}: ${opp.marketA.title.slice(0, 30)}`,
        siteAName: opp.marketA.platform,
        siteBName: opp.marketB.platform,
        siteAYesPrice: opp.marketA.yesPrice,
        siteBYesPrice: opp.marketB.yesPrice,
        investment: 500,
        alertThreshold: 3,
        isActive: true,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/watchlist"] });
      toast({ title: "Added to watchlist", description: "Market pair added for monitoring" });
    },
    onError: () => {
      toast({ title: "Failed to add", description: "Could not add to watchlist", variant: "destructive" });
    },
  });

  // Auto-refresh effect
  useEffect(() => {
    if (!autoRefresh || activeTab !== "opportunities") return;
    
    const intervalMs = parseInt(refreshInterval) * 60 * 1000;
    const timer = setInterval(() => {
      refetchOpps();
    }, intervalMs);
    
    return () => clearInterval(timer);
  }, [autoRefresh, refreshInterval, activeTab, refetchOpps]);

  const handleRefresh = () => {
    if (activeTab === "all") {
      refetchMarkets();
    } else {
      triggerScan();
    }
  };

  const isLoading = activeTab === "all" ? isMarketsLoading : isOppsLoading;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <CardTitle className="flex items-center gap-2">
            <Search className="w-5 h-5" />
            Live Market Browser
          </CardTitle>
          <div className="flex items-center gap-2">
            {lastRefresh && (
              <span className="text-xs text-muted-foreground">
                Updated: {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <Dialog open={manualPairOpen} onOpenChange={setManualPairOpen}>
              <DialogTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-[44px]"
                  data-testid="button-manual-pair"
                >
                  <Link2 className="w-4 h-4 mr-2" />
                  Pair Manually
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
                <DialogHeader>
                  <DialogTitle>Manual Market Pairing</DialogTitle>
                </DialogHeader>
                <div className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    Select two markets from different platforms to calculate arbitrage ROI.
                  </p>
                  
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Market A</Label>
                      <Input
                        placeholder="Search platform A..."
                        value={pairSearchA}
                        onChange={(e) => setPairSearchA(e.target.value)}
                        data-testid="input-pair-search-a"
                      />
                      {marketA && (
                        <div className="p-2 rounded border bg-muted/50">
                          <Badge className={platformColors[marketA.platform]}>{marketA.platform}</Badge>
                          <p className="text-sm mt-1 line-clamp-2">{marketA.title}</p>
                          <p className="text-xs font-mono">YES: ${marketA.yesPrice.toFixed(2)}</p>
                        </div>
                      )}
                      <ScrollArea className="h-40 border rounded">
                        {markets
                          .filter(m => m.title.toLowerCase().includes(pairSearchA.toLowerCase()))
                          .slice(0, 20)
                          .map(m => (
                            <div
                              key={m.id}
                              className={`p-2 cursor-pointer hover-elevate ${marketA?.id === m.id ? 'bg-primary/10' : ''}`}
                              onClick={() => setMarketA(m)}
                            >
                              <Badge className={platformColors[m.platform]} variant="secondary">{m.platform}</Badge>
                              <p className="text-xs line-clamp-1">{m.title}</p>
                            </div>
                          ))
                        }
                      </ScrollArea>
                    </div>
                    
                    <div className="space-y-2">
                      <Label>Market B</Label>
                      <Input
                        placeholder="Search platform B..."
                        value={pairSearchB}
                        onChange={(e) => setPairSearchB(e.target.value)}
                        data-testid="input-pair-search-b"
                      />
                      {marketB && (
                        <div className="p-2 rounded border bg-muted/50">
                          <Badge className={platformColors[marketB.platform]}>{marketB.platform}</Badge>
                          <p className="text-sm mt-1 line-clamp-2">{marketB.title}</p>
                          <p className="text-xs font-mono">YES: ${marketB.yesPrice.toFixed(2)}</p>
                        </div>
                      )}
                      <ScrollArea className="h-40 border rounded">
                        {markets
                          .filter(m => m.title.toLowerCase().includes(pairSearchB.toLowerCase()))
                          .filter(m => m.platform !== marketA?.platform)
                          .slice(0, 20)
                          .map(m => (
                            <div
                              key={m.id}
                              className={`p-2 cursor-pointer hover-elevate ${marketB?.id === m.id ? 'bg-primary/10' : ''}`}
                              onClick={() => setMarketB(m)}
                            >
                              <Badge className={platformColors[m.platform]} variant="secondary">{m.platform}</Badge>
                              <p className="text-xs line-clamp-1">{m.title}</p>
                            </div>
                          ))
                        }
                      </ScrollArea>
                    </div>
                  </div>
                  
                  {marketA && marketB && marketA.platform === marketB.platform && (
                    <div className="p-4 rounded-lg border border-destructive bg-destructive/10">
                      <p className="text-sm text-destructive font-medium">
                        Both markets are from the same platform ({marketA.platform}). 
                        Please select markets from different platforms for cross-platform arbitrage.
                      </p>
                    </div>
                  )}
                  
                  {manualPairPreview && marketA?.platform !== marketB?.platform && (
                    <div className="p-4 rounded-lg border bg-muted/30">
                      <h4 className="font-medium mb-2">ROI Preview</h4>
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <p className="text-sm text-muted-foreground">Scenario 1 (YES A + NO B)</p>
                          <p className={`text-lg font-mono font-bold ${manualPairPreview.roi1 > 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {manualPairPreview.roi1.toFixed(2)}%
                          </p>
                        </div>
                        <div>
                          <p className="text-sm text-muted-foreground">Scenario 2 (NO A + YES B)</p>
                          <p className={`text-lg font-mono font-bold ${manualPairPreview.roi2 > 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {manualPairPreview.roi2.toFixed(2)}%
                          </p>
                        </div>
                      </div>
                      {manualPairPreview.bestRoi > 0 && (
                        <Button
                          className="w-full mt-3 min-h-[44px]"
                          onClick={() => {
                            if (marketA && marketB) {
                              addToWatchlistMutation.mutate({
                                marketA,
                                marketB,
                                combinedYesCost: manualPairPreview.cost1,
                                potentialProfit: 1 - manualPairPreview.cost1,
                                roi: manualPairPreview.bestRoi,
                                matchScore: 100,
                                matchReason: "manual-pair"
                              });
                              setManualPairOpen(false);
                              setMarketA(null);
                              setMarketB(null);
                            }
                          }}
                          data-testid="button-add-manual-pair"
                        >
                          <Plus className="w-4 h-4 mr-2" />
                          Add to Watchlist ({manualPairPreview.bestRoi.toFixed(1)}% ROI)
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              </DialogContent>
            </Dialog>
            <Button
              variant="outline"
              size="sm"
              className="min-h-[44px]"
              onClick={handleRefresh}
              disabled={isLoading}
              data-testid="button-refresh-markets"
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${isLoading || isScanning ? "animate-spin" : ""}`} />
              {isScanning ? "Scanning..." : "Scan"}
            </Button>
            <BackendLogViewer />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2 flex-wrap">
          <Input
            placeholder="Search markets..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 min-w-[200px]"
            data-testid="input-market-search"
          />
        </div>

        {stats && stats.total > 0 && (
          <div className="flex items-center gap-4 p-3 rounded-md border bg-muted/30 flex-wrap">
            <span className="text-sm font-medium">Live Markets:</span>
            <Badge className={platformColors.Kalshi}>
              Kalshi: {stats.kalshi.toLocaleString()}
            </Badge>
            <Badge className={platformColors.Polymarket}>
              Polymarket: {stats.polymarket.toLocaleString()}
            </Badge>
            <Badge className={platformColors.PredictIt}>
              PredictIt: {stats.predictit.toLocaleString()}
            </Badge>
            {stats.ibkr > 0 && (
              <Badge className={platformColors.IBKR}>
                IBKR: {stats.ibkr.toLocaleString()}
              </Badge>
            )}
            <span className="text-sm text-muted-foreground ml-auto">
              Total: {stats.total.toLocaleString()} markets
            </span>
          </div>
        )}

        {(autoRefresh || isScanning) && (
          <div className="space-y-2" data-testid="scan-status">
            {isScanning ? (
              <div className={`p-4 rounded-md border space-y-3 transition-opacity duration-700 ${
                scanProgress?.percent === 100 ? 'bg-green-500/10 border-green-500/30 opacity-80' : 'bg-muted/30'
              }`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {scanProgress?.percent === 100 ? (
                      <div className="w-5 h-5 rounded-full bg-green-500 flex items-center justify-center">
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : (
                      <div className="relative w-5 h-5">
                        <div className="absolute inset-0 rounded-full border-2 border-green-500/30" />
                        <div className="absolute inset-0 rounded-full border-2 border-green-500 border-t-transparent animate-spin" />
                      </div>
                    )}
                    <div className="flex flex-col gap-0.5">
                      <span className="text-sm font-medium">
                        {scanProgress?.phase || 'Scanning markets...'}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {scanProgress?.message || 'Initializing...'}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge className="font-mono tabular-nums text-lg px-3 py-1 bg-green-600 text-white hover:bg-green-600">
                      {scanProgress?.percent ?? 0}%
                    </Badge>
                    <Badge variant="outline" className="font-mono tabular-nums">
                      {Math.floor(scanElapsed / 60)}:{(scanElapsed % 60).toString().padStart(2, '0')}
                    </Badge>
                  </div>
                </div>
                <div className="relative h-3 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`absolute inset-y-0 left-0 rounded-full transition-all duration-300 ease-out ${
                      scanProgress?.percent === 100 ? 'bg-green-500' : 'bg-green-500'
                    }`}
                    style={{ width: `${scanProgress?.percent ?? 0}%` }}
                    data-testid="progress-bar-fill"
                  />
                  {(scanProgress?.percent ?? 0) < 100 && (
                    <div
                      className="absolute inset-y-0 left-0 bg-green-400/30 rounded-full animate-pulse"
                      style={{ width: `${Math.min(100, (scanProgress?.percent ?? 0) + 3)}%` }}
                    />
                  )}
                </div>
                {scanProgress && scanProgress.totalComparisons > 0 && (
                  <div className="flex items-center gap-4 text-xs text-muted-foreground font-mono w-full">
                    <span>{scanProgress.totalMarkets.toLocaleString()} markets</span>
                    <span>{scanProgress.completedComparisons.toLocaleString()}/{scanProgress.totalComparisons.toLocaleString()} comparisons</span>
                    <span className="text-green-600 dark:text-green-400 font-semibold">{scanProgress.pairsFound} matches</span>
                    <BackendLogViewer />
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-3 p-3 rounded-md border bg-muted/30">
                <Badge variant="secondary">Auto-scan active</Badge>
                {lastRefresh && (
                  <span className="text-xs text-muted-foreground">
                    Every {refreshInterval} min
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="w-full grid grid-cols-3">
            <TabsTrigger value="opportunities" className="min-h-[44px] px-2 sm:px-4" data-testid="tab-opportunities">
              <TrendingUp className="w-4 h-4 mr-1 sm:mr-2 shrink-0" />
              <span className="truncate">
                <span className="hidden sm:inline">Arbitrage </span>Opportunities
              </span>
              <Badge variant="secondary" className="ml-1 sm:ml-2 shrink-0">{opportunities.length}</Badge>
            </TabsTrigger>
            <TabsTrigger value="visualizer" className="min-h-[44px] px-2 sm:px-4" data-testid="tab-visualizer">
              <BarChart3 className="w-4 h-4 mr-1 sm:mr-2 shrink-0" />
              <span>Visualizer</span>
            </TabsTrigger>
            <TabsTrigger value="all" className="min-h-[44px] px-2 sm:px-4" data-testid="tab-all-markets">
              <DollarSign className="w-4 h-4 mr-1 sm:mr-2 shrink-0" />
              <span className="hidden sm:inline">All </span>Markets
            </TabsTrigger>
          </TabsList>

          <TabsContent value="opportunities" className="mt-4 space-y-4">
            {/* Quick Presets */}
            <div className="flex gap-2 flex-wrap">
              {QUICK_PRESETS.map(preset => (
                <Button
                  key={preset.id}
                  variant={activePreset === preset.id ? "default" : "outline"}
                  size="sm"
                  className="min-h-[44px]"
                  onClick={() => applyPreset(preset.id)}
                  data-testid={`preset-${preset.id}`}
                >
                  {preset.id === "best-roi" && <TrendingUp className="w-4 h-4 mr-1" />}
                  {preset.id === "hot-opportunities" && <Flame className="w-4 h-4 mr-1" />}
                  {preset.id === "quick-wins" && <Zap className="w-4 h-4 mr-1" />}
                  {preset.id === "safe-bets" && <BarChart3 className="w-4 h-4 mr-1" />}
                  {preset.label}
                </Button>
              ))}
            </div>

            {/* Investment Amount Input */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 p-3 rounded-md border bg-muted/30">
              <div className="flex items-center gap-2">
                <DollarSign className="w-5 h-5 text-muted-foreground shrink-0" />
                <Label htmlFor="investment-amount" className="text-sm font-medium whitespace-nowrap">Investment:</Label>
                <Input
                  id="investment-amount"
                  type="number"
                  value={investmentAmount}
                  onChange={(e) => setInvestmentAmount(e.target.value)}
                  placeholder="100"
                  className="w-24 sm:w-28 min-h-[44px]"
                  data-testid="input-investment-amount"
                />
              </div>
              <span className="text-xs sm:text-sm text-muted-foreground">Profit shown for each opportunity below</span>
            </div>

            {/* Sorting and Filtering Controls */}
            <Collapsible open={showFilters} onOpenChange={setShowFilters}>
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
                <div className="flex items-center gap-2 flex-wrap">
                  <Label className="text-sm font-medium shrink-0">Sort by:</Label>
                  <Select value={sortBy} onValueChange={(v: string) => { setSortBy(v as SortOption); setActivePreset(null); }}>
                    <SelectTrigger className="w-40 sm:w-48 min-h-[44px]" data-testid="select-sort">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SORT_OPTIONS.map(opt => (
                        <SelectItem key={opt.value} value={opt.value}>
                          <div className="flex items-center gap-2">
                            <opt.icon className="w-4 h-4" />
                            {opt.label}
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <CollapsibleTrigger asChild>
                    <Button variant="outline" size="sm" className="min-h-[44px]" data-testid="button-toggle-filters">
                      <Filter className="w-4 h-4 mr-1 sm:mr-2" />
                      <span className="hidden sm:inline">Filters</span>
                      <span className="sm:hidden">Filter</span>
                      {minRoiFilter > 0 && <Badge variant="secondary" className="ml-1 sm:ml-2">{minRoiFilter}%+</Badge>}
                    </Button>
                  </CollapsibleTrigger>
                </div>
                
                <span className="text-xs sm:text-sm text-muted-foreground sm:ml-auto">
                  Showing {sortedOpportunities.length} of {opportunities.length}{dismissedPairs.size > 0 ? ` (${dismissedPairs.size} hidden)` : ''}
                </span>
              </div>
              
              <CollapsibleContent className="mt-4 p-4 rounded-md border bg-muted/20">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="text-sm">Minimum ROI</Label>
                      <span className="text-sm font-mono bg-muted px-2 py-0.5 rounded">{minRoiFilter}%</span>
                    </div>
                    <Slider
                      value={[minRoiFilter]}
                      onValueChange={([v]: number[]) => { setMinRoiFilter(v); setActivePreset(null); }}
                      min={0}
                      max={10}
                      step={0.5}
                      className="w-full"
                      data-testid="slider-min-roi"
                    />
                  </div>
                  
                  {availableCategories.length > 0 && (
                    <div className="space-y-2">
                      <Label className="text-sm">Categories</Label>
                      <div className="flex gap-2 flex-wrap">
                        {availableCategories.map(cat => (
                          <Button
                            key={cat}
                            variant={selectedCategories.includes(cat) ? "default" : "outline"}
                            size="sm"
                            onClick={() => setSelectedCategories(prev => 
                              prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
                            )}
                            data-testid={`filter-category-${cat}`}
                          >
                            {cat}
                          </Button>
                        ))}
                      </div>
                    </div>
                  )}
                  
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { setMinRoiFilter(0); setSelectedCategories([]); setActivePreset(null); }}
                    data-testid="button-clear-filters"
                  >
                    Clear all filters
                  </Button>
                </div>
              </CollapsibleContent>
            </Collapsible>

            {/* Non-blocking scan indicator — shown above results, never hides them */}
            {(isOppsLoading || isScanning) && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-muted/40 border text-sm text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                <span>
                  {isScanning
                    ? `Refreshing prices across ${enabledPlatforms.length} platforms...`
                    : "Loading opportunities..."}
                </span>
              </div>
            )}
            {sortedOpportunities.length === 0 && !isOppsLoading && !isScanning ? (
              <div className="text-center py-8 text-muted-foreground space-y-2">
                {opportunities.length > 0 ? (
                  <>
                    <p>No opportunities match your current filters.</p>
                    <p className="text-sm">Try lowering the minimum ROI or clearing filters.</p>
                  </>
                ) : (
                  <>
                    <p>No matching markets found yet.</p>
                    <p className="text-sm">Hit Scan to fetch live markets and find cross-platform arbitrage opportunities.</p>
                  </>
                )}
              </div>
            ) : (
              <>
                <ScrollArea className="h-[65vh]">
                  <div className="w-full overflow-hidden border rounded-lg bg-card">
                    <table className="w-full text-sm text-left whitespace-nowrap">
                      <thead className="text-[11px] text-muted-foreground uppercase bg-muted/40 border-b relative z-10 sticky top-0">
                        <tr>
                          <th className="px-3 py-2.5 font-medium">Expiry</th>
                          <th className="px-3 py-2.5 font-medium w-[30%]">Source Market</th>
                          <th className="px-3 py-2.5 font-medium w-[30%]">Matched Market</th>
                          <th className="px-3 py-2.5 font-medium text-center">Score</th>
                          <th className="px-3 py-2.5 font-medium text-right">Target ROI</th>
                          <th className="px-3 py-2.5 font-medium text-center">Feedback</th>
                          <th className="px-3 py-2.5 font-medium text-center">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/40">
                        {paginatedOpportunities.map((opp, idx) => {
                          const pairKey = `${opp.marketA.id}-${opp.marketB.id}`;
                          const currentVerdict = ratedPairs.get(pairKey);
                          const inv = parseFloat(investmentAmount) || 0;
                          const profit = inv > 0 && opp.roi > 0 && opp.roi < 5000 ? (inv * opp.roi / 100) : null;
                          const noA = opp.marketA.noPrice ?? (1 - opp.marketA.yesPrice);
                          const noB = opp.marketB.noPrice ?? (1 - opp.marketB.yesPrice);
                          return (
                            <tr
                              key={idx}
                              className={`group transition-colors hover:bg-muted/20 ${opp.roi >= 3 ? "bg-green-500/[0.02]" : ""}`}
                              data-testid={`row-opportunity-${idx}`}
                            >
                              <td className="px-3 py-2 align-top pt-3">
                                {opp.daysToExpiry !== null && opp.daysToExpiry >= 0 ? (
                                  <Badge variant={opp.daysToExpiry <= 2 ? "destructive" : "outline"} className="text-[10px] px-1.5 font-medium">
                                    {opp.daysToExpiry}d
                                  </Badge>
                                ) : (
                                  <span className="text-muted-foreground text-xs">-</span>
                                )}
                              </td>
                              <td className="px-3 py-2 align-top whitespace-normal min-w-[200px] max-w-[300px]">
                                <div className="flex items-center gap-1.5 mb-1">
                                  <span className={`text-[10px] font-bold uppercase tracking-wide ${platformColors[opp.marketA.platform] || 'text-foreground'}`}>
                                    {opp.marketA.platform.replace(' Forecast', '')}
                                  </span>
                                  {opp.marketA.endDate && formatExpiry(opp.marketA.endDate) && (
                                    <Badge variant="outline" className="text-[9px] px-1 py-0 h-4">
                                      <Clock className="w-2.5 h-2.5 mr-0.5" />
                                      {formatExpiry(opp.marketA.endDate)}
                                    </Badge>
                                  )}
                                  {opp.marketA.marketUrl && (
                                    <a href={opp.marketA.marketUrl} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-primary transition-colors">
                                      <ExternalLink className="w-3 h-3" />
                                    </a>
                                  )}
                                </div>
                                <div className="text-[12px] leading-snug font-medium text-foreground/90 mb-1.5 line-clamp-2" title={opp.marketA.title}>
                                  {opp.marketA.title}
                                </div>
                                <div className="flex gap-2 text-[11px] font-mono">
                                  {opp.marketA.contractLabel && opp.marketA.contractLabel !== "Yes" ? (
                                    <span className="bg-green-500/10 text-green-700 dark:text-green-400 px-1.5 py-0.5 rounded truncate max-w-[120px]" title={opp.marketA.contractLabel}>
                                      {opp.marketA.contractLabel}: {(opp.marketA.yesPrice * 100).toFixed(0)}¢
                                    </span>
                                  ) : (
                                    <>
                                      <span className="bg-green-500/10 text-green-700 dark:text-green-400 px-1.5 py-0.5 rounded">Y: {(opp.marketA.yesPrice * 100).toFixed(0)}¢</span>
                                      <span className="bg-red-500/10 text-red-700 dark:text-red-400 px-1.5 py-0.5 rounded">N: {(noA * 100).toFixed(0)}¢</span>
                                    </>
                                  )}
                                </div>
                              </td>
                              <td className="px-3 py-2 align-top whitespace-normal min-w-[200px] max-w-[300px]">
                                <div className="flex items-center gap-1.5 mb-1">
                                  <span className={`text-[10px] font-bold uppercase tracking-wide ${platformColors[opp.marketB.platform] || 'text-foreground'}`}>
                                    {opp.marketB.platform.replace(' Forecast', '')}
                                  </span>
                                  {opp.marketB.endDate && formatExpiry(opp.marketB.endDate) && (
                                    <Badge variant="outline" className="text-[9px] px-1 py-0 h-4">
                                      <Clock className="w-2.5 h-2.5 mr-0.5" />
                                      {formatExpiry(opp.marketB.endDate)}
                                    </Badge>
                                  )}
                                  {opp.marketB.marketUrl && (
                                    <a href={opp.marketB.marketUrl} target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-primary transition-colors">
                                      <ExternalLink className="w-3 h-3" />
                                    </a>
                                  )}
                                </div>
                                <div className="text-[12px] leading-snug font-medium text-foreground/90 mb-1.5 line-clamp-2" title={opp.marketB.title}>
                                  {opp.marketB.title}
                                </div>
                                <div className="flex gap-2 text-[11px] font-mono">
                                  {opp.marketB.contractLabel && opp.marketB.contractLabel !== "Yes" ? (
                                    <span className="bg-green-500/10 text-green-700 dark:text-green-400 px-1.5 py-0.5 rounded truncate max-w-[120px]" title={opp.marketB.contractLabel}>
                                      {opp.marketB.contractLabel}: {(opp.marketB.yesPrice * 100).toFixed(0)}¢
                                    </span>
                                  ) : (
                                    <>
                                      <span className="bg-green-500/10 text-green-700 dark:text-green-400 px-1.5 py-0.5 rounded">Y: {(opp.marketB.yesPrice * 100).toFixed(0)}¢</span>
                                      <span className="bg-red-500/10 text-red-700 dark:text-red-400 px-1.5 py-0.5 rounded">N: {(noB * 100).toFixed(0)}¢</span>
                                    </>
                                  )}
                                </div>
                              </td>
                              <td className="px-3 py-2 align-top pt-3 text-center">
                                <div className="flex flex-col items-center gap-1.5">
                                  {opp.isVerified && (
                                    <Badge className="bg-blue-600 dark:bg-blue-500 text-white border-0 text-[9px] font-bold uppercase tracking-tight px-1.5 py-0 h-4.5 flex items-center gap-0.5 shadow-sm">
                                      <Zap className="w-2.5 h-2.5 fill-white" /> Verified
                                    </Badge>
                                  )}
                                  <div className="flex items-center justify-center gap-1.5">
                                    <Badge variant="secondary" className="text-[11px] font-medium bg-muted/60">{Math.round(opp.opportunityScore)}%</Badge>
                                    {(opp.matchReason || opp.matchScore) && (
                                      <Dialog>
                                        <DialogTrigger asChild>
                                          <Button size="icon" variant="ghost" className="w-5 h-5 rounded-full hover:bg-blue-500/10 hover:text-blue-600 transition-colors">
                                            <HelpCircle className="w-3.5 h-3.5" />
                                          </Button>
                                        </DialogTrigger>
                                      <DialogContent className="sm:max-w-[425px]">
                                        <DialogHeader>
                                          <DialogTitle className="flex items-center gap-2 text-blue-600">
                                            <Terminal className="w-5 h-5" />
                                            AI Matching Reasoning
                                          </DialogTitle>
                                        </DialogHeader>
                                        <div className="space-y-4 py-4">
                                          <div className="p-3 rounded-md bg-muted/50 border">
                                            <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Similarity Score</div>
                                            <div className="text-2xl font-bold text-blue-600">
                                              {Math.round(opp.opportunityScore)}% Match Efficiency
                                            </div>
                                          </div>
                                          
                                          <div className="space-y-2">
                                            <div className="text-xs font-semibold text-muted-foreground uppercase">Matching Logic</div>
                                            <div className="text-sm leading-relaxed p-4 rounded-md bg-blue-500/[0.03] border border-blue-500/10 italic">
                                              "{opp.matchReason || "The AI matched these based on semantic similarity of the titles and outcome structures."}"
                                            </div>
                                          </div>

                                          <div className="text-xs text-muted-foreground p-3 border rounded border-dashed">
                                            <span className="font-bold">Pro Tip:</span> This match was automatically found by our Cloud GPU matcher using advanced sentence embeddings.
                                          </div>
                                        </div>
                                      </DialogContent>
                                    </Dialog>
                                  )}
                                  </div>
                                </div>
                              </td>
                              <td className="px-3 py-2 align-top pt-3 text-right">
                                <div className={`text-[13px] font-bold tabular-nums ${opp.roi >= 500 ? "text-muted-foreground" : opp.roi >= 3 ? "text-green-600 dark:text-green-400" : "text-blue-600 dark:text-blue-400"}`}>
                                  {opp.roi >= 1000 ? `${(opp.roi/1000).toFixed(1)}k%` : `+${opp.roi.toFixed(1)}%`}
                                </div>
                                {profit !== null && (
                                  <div className="text-[11px] font-semibold text-green-600 dark:text-green-400 block mt-0.5">
                                    +${profit.toFixed(2)}
                                  </div>
                                )}
                              </td>
                              <td className="px-3 py-2 align-top pt-3">
                                <div className="flex items-center justify-center gap-1">
                                  <Button size="icon" variant={currentVerdict === 'approve' ? 'default' : 'ghost'} className={`w-7 h-7 rounded-md ${currentVerdict === 'approve' ? 'bg-green-600 hover:bg-green-700' : 'text-muted-foreground hover:bg-green-500/10 hover:text-green-600'}`} onClick={() => feedbackMutation.mutate({ opportunity: opp, verdict: 'approve' })} title="Good match">
                                    <ThumbsUp className="w-3.5 h-3.5" />
                                  </Button>
                                  <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                      <Button size="icon" variant={currentVerdict === 'reject' ? 'default' : 'ghost'} className={`w-7 h-7 rounded-md ${currentVerdict === 'reject' ? 'bg-red-600 hover:bg-red-700' : 'text-muted-foreground hover:bg-red-500/10 hover:text-red-600'}`} title="Bad match">
                                        <ThumbsDown className="w-3.5 h-3.5" />
                                      </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="end" className="text-xs">
                                      <DropdownMenuItem className="text-xs cursor-pointer" onClick={() => feedbackMutation.mutate({ opportunity: { ...opp, matchReason: 'Numerical Mismatch' }, verdict: 'reject' })}>
                                        Numerical Mismatch (5.5 vs 6+)
                                      </DropdownMenuItem>
                                      <DropdownMenuItem className="text-xs cursor-pointer" onClick={() => feedbackMutation.mutate({ opportunity: { ...opp, matchReason: 'Different Context/Event' }, verdict: 'reject' })}>
                                        Different Context / Event
                                      </DropdownMenuItem>
                                      <DropdownMenuItem className="text-xs cursor-pointer" onClick={() => feedbackMutation.mutate({ opportunity: { ...opp, matchReason: 'Different Date/Time' }, verdict: 'reject' })}>
                                        Different Date / Time
                                      </DropdownMenuItem>
                                      <DropdownMenuItem className="text-xs cursor-pointer" onClick={() => feedbackMutation.mutate({ opportunity: opp, verdict: 'reject' })}>
                                        Other mismatch
                                      </DropdownMenuItem>
                                    </DropdownMenuContent>
                                  </DropdownMenu>
                                  <Button size="icon" variant="ghost" className="w-7 h-7 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity" onClick={() => feedbackMutation.mutate({ opportunity: opp, verdict: 'not_interested' })} title="Hide">
                                    <EyeOff className="w-3.5 h-3.5" />
                                  </Button>
                                </div>
                              </td>
                              <td className="px-3 py-2 align-top pt-3 text-center">
                                <div className="flex items-center justify-center gap-1.5">
                                  <Button size="sm" variant="default" className="h-7 px-2.5 text-[11px] font-medium tracking-wide shadow-sm" onClick={() => openBothMarkets(opp.marketA.marketUrl || '', opp.marketB.marketUrl || '', toast)} disabled={!opp.marketA.marketUrl || !opp.marketB.marketUrl}>
                                    Open Both
                                  </Button>
                                  <Button size="sm" variant="outline" className="h-7 px-2 text-[11px] font-medium tracking-wide shadow-sm border-indigo-500/20 hover:border-indigo-500/50 hover:bg-indigo-500/[0.04] text-indigo-600 dark:text-indigo-400" onClick={() => setSelectedHistoryOpp(opp)}>
                                    <BarChart3 className="w-3.5 h-3.5 mr-1" /> History
                                  </Button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </ScrollArea>
                {totalPages > 1 && (
                  <div className="flex justify-center items-center gap-2 mt-4 pb-4">
                    <Button 
                      variant="outline" 
                      size="sm" 
                      disabled={currentPage === 1}
                      onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                    >
                      Previous
                    </Button>
                    {[...Array(totalPages)].map((_, i) => (
                      <Button
                        key={i}
                        variant={currentPage === i + 1 ? "default" : "outline"}
                        size="sm"
                        className="w-8"
                        onClick={() => setCurrentPage(i + 1)}
                      >
                        {i + 1}
                      </Button>
                    ))}
                    <Button 
                      variant="outline" 
                      size="sm" 
                      disabled={currentPage === totalPages}
                      onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
                    >
                      Next
                    </Button>
                  </div>
                )}
              </>
            )}
          </TabsContent>

          <TabsContent value="visualizer" className="mt-4 space-y-6">
            {sortedOpportunities.length === 0 ? (
              <Card className="border-dashed">
                <CardContent className="py-12 text-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="p-3 rounded-full bg-muted">
                      <BarChart3 className="w-6 h-6 text-muted-foreground" />
                    </div>
                    <div>
                      <p className="text-muted-foreground font-medium">
                        No opportunities to visualize
                      </p>
                      <p className="text-sm text-muted-foreground/70">
                        Try clearing search filters or scanning to find active opportunities.
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ) : (
              <>
                {/* Visualizer Stat Summary */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <Card className="bg-emerald-50/50 dark:bg-emerald-950/20 border-emerald-500/10">
                    <CardHeader className="py-3 pb-1">
                      <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                        Highest Available ROI
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="pb-3">
                      <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                        {Math.max(...sortedOpportunities.map(o => o.roi)).toFixed(2)}%
                      </div>
                    </CardContent>
                  </Card>
                  
                  <Card className="bg-sky-50/50 dark:bg-sky-950/20 border-sky-500/10">
                    <CardHeader className="py-3 pb-1">
                      <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                        Average Opportunity Score
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="pb-3">
                      <div className="text-2xl font-bold text-sky-600 dark:text-sky-400">
                        {(sortedOpportunities.reduce((acc, curr) => acc + curr.opportunityScore, 0) / sortedOpportunities.length).toFixed(1)}/100
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="bg-indigo-50/50 dark:bg-indigo-950/20 border-indigo-500/10">
                    <CardHeader className="py-3 pb-1">
                      <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                        Exchanges Scanned
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="pb-3">
                      <div className="text-2xl font-bold text-indigo-600 dark:text-indigo-400">
                        {new Set(sortedOpportunities.flatMap(o => [o.marketA.platform, o.marketB.platform])).size} Platforms
                      </div>
                    </CardContent>
                  </Card>
                </div>

                {/* Primary Chart Row */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                  {/* Bar Chart: ROI comparison */}
                  <Card className="lg:col-span-2">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-semibold flex items-center gap-2">
                        <TrendingUp className="w-4 h-4 text-emerald-500" />
                        Top Arbitrage Opportunities by ROI
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="h-[300px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                          layout="vertical"
                          data={sortedOpportunities.slice(0, 7).map(opp => ({
                            name: opp.marketA.title.length > 25 ? opp.marketA.title.substring(0, 25) + "..." : opp.marketA.title,
                            fullTitle: opp.marketA.title,
                            roi: parseFloat(opp.roi.toFixed(2)),
                            platforms: `${opp.marketA.platform} vs ${opp.marketB.platform}`
                          }))}
                          margin={{ top: 10, right: 30, left: 10, bottom: 5 }}
                        >
                          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                          <XAxis type="number" unit="%" />
                          <YAxis dataKey="name" type="category" width={120} tick={{ fontSize: 11 }} />
                          <Tooltip 
                            formatter={(value: any, name: any, props: any) => [`${value}%`, "Net ROI"]}
                            labelFormatter={(label, items) => items[0]?.payload?.fullTitle || label}
                          />
                          <Bar dataKey="roi" radius={[0, 4, 4, 0]}>
                            {sortedOpportunities.slice(0, 7).map((opp, index) => (
                              <Cell 
                                key={`cell-${index}`} 
                                fill={opp.roi >= 5 ? "#10b981" : opp.roi >= 2 ? "#14b8a6" : "#6366f1"} 
                              />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>

                  {/* Pie Chart: Exchange Combinations */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-semibold flex items-center gap-2">
                        <Layers className="w-4 h-4 text-indigo-500" />
                        Platform Distribution
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="h-[300px] flex flex-col justify-between">
                      <div className="flex-1 min-h-0">
                        <ResponsiveContainer width="100%" height="100%">
                          <PieChart>
                            <Pie
                              data={(() => {
                                const pairsMap: Record<string, number> = {};
                                sortedOpportunities.forEach(opp => {
                                  const key = `${opp.marketA.platform} / ${opp.marketB.platform}`;
                                  pairsMap[key] = (pairsMap[key] || 0) + 1;
                                });
                                return Object.entries(pairsMap).map(([name, value]) => ({ name, value }));
                              })()}
                              cx="50%"
                              cy="50%"
                              innerRadius={60}
                              outerRadius={80}
                              paddingAngle={5}
                              dataKey="value"
                            >
                              {(() => {
                                const pairsMap: Record<string, number> = {};
                                sortedOpportunities.forEach(opp => {
                                  const key = `${opp.marketA.platform} / ${opp.marketB.platform}`;
                                  pairsMap[key] = (pairsMap[key] || 0) + 1;
                                });
                                return Object.entries(pairsMap).map((_, index) => (
                                  <Cell key={`cell-${index}`} fill={["#6366f1", "#06b6d4", "#ec4899", "#f59e0b"][index % 4]} />
                                ));
                              })()}
                            </Pie>
                            <Tooltip formatter={(value) => [value, "Opportunities"]} />
                            <Legend verticalAlign="bottom" height={36} iconSize={10} iconType="circle" wrapperStyle={{ fontSize: 10 }} />
                          </PieChart>
                        </ResponsiveContainer>
                      </div>
                    </CardContent>
                  </Card>
                </div>

                {/* Risk/Reward Scatter Map */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-semibold flex items-center gap-2">
                      <Zap className="w-4 h-4 text-amber-500" />
                      Risk / Reward Efficiency Map
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Sweeps match confidence against return. Target the top-right quadrant for high-confidence, high-yield trades.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="h-[350px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <ScatterChart
                        margin={{ top: 20, right: 30, bottom: 20, left: 10 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis 
                          type="number" 
                          dataKey="confidence" 
                          name="Match Confidence" 
                          unit="%" 
                          domain={[60, 100]} 
                          label={{ value: "Match Confidence (%)", position: "bottom", offset: 0, fontSize: 11 }}
                        />
                        <YAxis 
                          type="number" 
                          dataKey="roi" 
                          name="Net ROI" 
                          unit="%" 
                          label={{ value: "Net ROI (%)", angle: -90, position: "left", offset: 0, fontSize: 11 }}
                        />
                        <Tooltip 
                          cursor={{ strokeDasharray: "3 3" }} 
                          formatter={(value: any, name: any, props: any) => [`${value}%`, name]}
                          labelFormatter={(label) => `Opportunity`}
                        />
                        <Scatter 
                          name="Opportunities" 
                          data={sortedOpportunities.map(opp => ({
                            confidence: opp.opportunityScore,
                            roi: parseFloat(opp.roi.toFixed(2)),
                            name: opp.marketA.title,
                            isVerified: opp.isVerified
                          }))} 
                        >
                          {sortedOpportunities.map((opp, index) => (
                            <Cell 
                              key={`cell-${index}`} 
                              fill={opp.isVerified ? "#10b981" : "#6366f1"} 
                              radius={6}
                            />
                          ))}
                        </Scatter>
                      </ScatterChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>
              </>
            )}
          </TabsContent>

          <TabsContent value="all" className="mt-4">
            {isMarketsLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                <span className="ml-2 text-muted-foreground">Loading markets...</span>
              </div>
            ) : markets.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No markets found. Try a different search.
              </div>
            ) : (
              <AllMarketsGrid markets={markets} platformColors={platformColors} />
            )}
          </TabsContent>
        </Tabs>

        <Dialog open={!!selectedHistoryOpp} onOpenChange={(open: boolean) => !open && setSelectedHistoryOpp(null)}>
          <DialogContent className="max-w-4xl max-h-[90vh] overflow-auto">
            {selectedHistoryOpp && (
              <OpportunityHistoryModal 
                opp={selectedHistoryOpp} 
                onClose={() => setSelectedHistoryOpp(null)} 
              />
            )}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}

interface OpportunityHistoryModalProps {
  opp: EnrichedOpportunity;
  onClose: () => void;
}

interface LeadLagStat {
  leader: string;
  shift_minutes: number;
}

interface OpportunityHistoryPayload {
  time_series: Array<Record<string, string | number>>;
  metrics: {
    lead_lag?: Record<string, LeadLagStat>;
    current_z_score: number;
    avg_roi: number;
  };
  platforms: string[];
}

function OpportunityHistoryModal({ opp, onClose }: OpportunityHistoryModalProps) {
  const { data: historyData, isLoading, error } = useQuery<OpportunityHistoryPayload>({
    queryKey: ["/api/arbitrage-opportunities/history", opp.marketA.id, opp.marketB.id],
    queryFn: async () => {
      const res = await fetch(`/api/arbitrage-opportunities/history?market_a_id=${opp.marketA.id}&market_b_id=${opp.marketB.id}`);
      if (!res.ok) throw new Error("Failed to fetch historical data");
      return res.json();
    },
    enabled: !!opp,
  });

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center p-12 gap-3 min-h-[300px]">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Running Pandas alignment engine...</p>
      </div>
    );
  }

  if (error || !historyData) {
    return (
      <div className="p-8 text-center text-destructive">
        Failed to load historical spread analysis.
      </div>
    );
  }

  const { time_series, metrics, platforms } = historyData;

  // Platform styling helper
  const platformColorsMap: Record<string, string> = {
    Polymarket: "#6366f1",
    PredictIt: "#f59e0b",
    IBKR: "#f43f5e",
  };

  const formatChartDate = (isoStr: string) => {
    try {
      const date = new Date(isoStr);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoStr;
    }
  };

  return (
    <div className="space-y-6 py-2">
      <div>
        <h3 className="text-lg font-bold text-foreground leading-snug line-clamp-1">{opp.marketA.title}</h3>
        <div className="flex flex-wrap gap-2 items-center mt-1">
          <Badge variant="outline" className="text-[10px] font-mono">
            {opp.marketA.platform} vs {opp.marketB.platform}
          </Badge>
          {opp.isVerified && (
            <Badge className="bg-blue-600 text-white text-[9px] h-5">
              <Zap className="w-2.5 h-2.5 mr-0.5" /> Verified
            </Badge>
          )}
        </div>
      </div>

      {/* Pandas Metrics Panel */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="p-3.5 rounded-lg border bg-muted/20">
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            Lead-Lag Leader (Pandas)
          </div>
          <div className="text-base font-bold text-foreground mt-1">
            {metrics.lead_lag && Object.keys(metrics.lead_lag).length > 0 ? (
              <span className="text-indigo-600 dark:text-indigo-400">
                {Object.values(metrics.lead_lag)[0].leader} <span className="text-xs text-muted-foreground font-medium">({Math.abs(Object.values(metrics.lead_lag)[0].shift_minutes)}m lead)</span>
              </span>
            ) : (
              <span className="text-muted-foreground text-sm font-medium">In Sync / Active Discovery</span>
            )}
          </div>
        </div>

        <div className="p-3.5 rounded-lg border bg-muted/20">
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1">
            <Layers className="w-3.5 h-3.5 text-indigo-500" />
            Spread Z-Score
          </div>
          <div className="text-base font-bold text-foreground mt-1">
            <span className={Math.abs(metrics.current_z_score) >= 1.5 ? "text-green-600 dark:text-green-400" : ""}>
              {metrics.current_z_score.toFixed(2)}
            </span>
          </div>
        </div>

        <div className="p-3.5 rounded-lg border bg-muted/20">
          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-500" />
            Avg Arbitrage ROI
          </div>
          <div className="text-base font-bold text-emerald-600 dark:text-emerald-400 mt-1">
            +{metrics.avg_roi.toFixed(2)}% ROI
          </div>
        </div>
      </div>

      {/* Time Series Charts */}
      <div className="space-y-5">
        {/* Price Tracking Line Chart */}
        <Card className="border shadow-none">
          <CardHeader className="py-3 px-4 flex flex-row items-center justify-between border-b bg-muted/10">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Implied Probabilities (YES Price History)
            </CardTitle>
            <div className="flex gap-3 text-xs">
              {platforms.map(p => (
                <div key={p} className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: platformColorsMap[p] || "#ccc" }} />
                  <span className="text-muted-foreground">{p}</span>
                </div>
              ))}
            </div>
          </CardHeader>
          <CardContent className="h-[250px] p-4">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={time_series} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="timestamp" tickFormatter={formatChartDate} tick={{ fontSize: 10 }} />
                <YAxis domain={[0.0, 1.0]} tickFormatter={(val) => `${(val * 100).toFixed(0)}¢`} tick={{ fontSize: 10 }} />
                <Tooltip 
                  labelFormatter={(lbl) => `Time: ${new Date(lbl).toLocaleString()}`}
                  formatter={(val: any, name: any) => [`${(val * 100).toFixed(0)}¢`, name]}
                />
                {platforms.map(p => (
                  <Line
                    key={p}
                    type="monotone"
                    dataKey={p}
                    stroke={platformColorsMap[p] || "#ccc"}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        {/* Spread Area Chart */}
        <Card className="border shadow-none">
          <CardHeader className="py-3 px-4 border-b bg-muted/10">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Arbitrage ROI Spread %
            </CardTitle>
          </CardHeader>
          <CardContent className="h-[150px] p-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={time_series} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorRoi" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0.0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="timestamp" tickFormatter={formatChartDate} tick={{ fontSize: 10 }} />
                <YAxis tickFormatter={(val) => `${val.toFixed(1)}%`} tick={{ fontSize: 10 }} />
                <Tooltip 
                  labelFormatter={(lbl) => `Time: ${new Date(lbl).toLocaleString()}`}
                  formatter={(val: any) => [`${val.toFixed(2)}%`, "Arb ROI"]}
                />
                <Area 
                  type="monotone" 
                  dataKey="roi" 
                  stroke="#10b981" 
                  strokeWidth={2}
                  fillOpacity={1} 
                  fill="url(#colorRoi)" 
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
