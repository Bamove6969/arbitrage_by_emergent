import { useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageHeader } from "@/components/terminal";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { 
  TrendingUp, 
  TrendingDown, 
  DollarSign, 
  AlertTriangle,
  CircleCheck,
  CircleX,
  RefreshCw,
  Percent,
  Calculator,
  Copy,
  Check,
  ShoppingCart,
  Shield,
  Zap,
  Info,
  Save,
  History,
  Eye,
  ExternalLink,
  Columns
} from "lucide-react";
import { Link } from "wouter";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { useToast } from "@/hooks/use-toast";
import { openMarketsInSplitScreen } from "@/lib/split-screen";
import type { InsertArbitrageHistory } from "@shared/schema";

type OrderMode = "Maker" | "Taker";

interface ArbitrageResult {
  scenario: string;
  pairLabel: string;
  action1: string;
  action2: string;
  price1: number;
  price2: number;
  cost: number;
  grossProfit: number;
  fees: number;
  netProfit: number;
  grossRoi: number;
  netRoi: number;
  isProfitable: boolean;
  shares: number;
  totalInvestment: number;
  totalGrossProfit: number;
  totalFees: number;
  totalNetProfit: number;
  siteAName: string;
  siteBName: string;
  siteAYesPrice: number;
  siteBYesPrice: number;
  viewLink?: string;
}

const PLATFORMS = [
  { id: "kalshi", name: "Kalshi" },
  { id: "polymarket", name: "Polymarket" },
  { id: "predictit", name: "PredictIt" },
  { id: "ibkr", name: "IBKR ForecastEx" },
];

export default function ArbitrageCalculator() {
  const [siteAName, setSiteAName] = useState("Kalshi");
  const [siteBName, setSiteBName] = useState("Polymarket");
  const [siteCName, setSiteCName] = useState("PredictIt");
  const [siteDName, setSiteDName] = useState("IBKR ForecastEx");
  const [siteAYes, setSiteAYes] = useState("");
  const [siteBYes, setSiteBYes] = useState("");
  const [siteCYes, setSiteCYes] = useState("");
  const [siteDYes, setSiteDYes] = useState("");
  const [investment, setInvestment] = useState("100");
  const [orderMode, setOrderMode] = useState<OrderMode>("Maker");
  const [copiedScenario, setCopiedScenario] = useState<number | null>(null);
  const [savedScenario, setSavedScenario] = useState<number | null>(null);
  
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Get platform URL for viewing markets
  const getPlatformUrl = (platform: string): string => {
    const p = platform.toLowerCase();
    if (p === "kalshi") return "https://kalshi.com/markets";
    if (p === "polymarket") return "https://polymarket.com";
    if (p === "predictit") return "https://www.predictit.org";
    if (p.includes("ibkr")) return "https://forecasttrader.interactivebrokers.com";
    return "#";
  };

  // Smart split-screen opener - MUST be synchronous from click handler
  const openSideBySide = (url1: string, url2: string) => {
    // Validate URLs before opening
    if (!url1.startsWith('http') || !url2.startsWith('http')) {
      toast({
        title: "Invalid platform",
        description: "This platform doesn't have a browsable market link",
        variant: "destructive"
      });
      return;
    }
    
    // Use shared utility - synchronous to avoid popup blocking
    const result = openMarketsInSplitScreen(url1, url2);
    
    if (result.method === 'clipboard') {
      toast({
        title: "Popup blocked",
        description: result.message || "URLs copied to clipboard",
        variant: "destructive"
      });
    } else if (result.method === 'split') {
      toast({
        title: "Markets opened",
        description: "Both markets opened side-by-side"
      });
    } else {
      toast({
        title: "Markets opened",
        description: "Both markets opened in new tabs"
      });
    }
  };

  const showPredictItWarning = useMemo(() => {
    const names = [siteAName.toLowerCase(), siteBName.toLowerCase(), siteCName.toLowerCase(), siteDName.toLowerCase()];
    return names.some(name => name.includes("predictit"));
  }, [siteAName, siteBName, siteCName, siteDName]);

  // Platform-specific fee calculation (2026 Updated)
  const calculatePlatformFee = (platform: string, price: number, contracts: number, mode: "Maker" | "Taker"): number => {
    const platformLower = platform.toLowerCase();
    
    // Safety check for invalid price
    if (price <= 0 || price >= 1) return 0;

    if (platformLower === "kalshi") {
      // Kalshi 2026: Variable fee based on contract price p*(1-p)
      // Taker: 7% multiplier, Maker: 1.75% multiplier
      const multiplier = mode === "Taker" ? 0.07 : 0.0175;
      const feePerContract = multiplier * price * (1 - price);
      
      // Caps: Taker $1.75 per 100 contracts ($0.0175), Maker $0.44 per 100 ($0.0044)
      const cap = mode === "Taker" ? 0.0175 : 0.0044;
      return Math.min(cap, feePerContract) * contracts;

    } else if (platformLower === "polymarket") {
      // Polymarket 2026: 0% fee on most standard markets
      // (Only 15-min crypto and specific sports have taker fees, defaulting to 0 for general arb)
      return 0;

    } else if (platformLower === "predictit") {
      // PredictIt: 10% on profit (if contract wins) + 5% on withdrawal
      // We factor this in as a cost against the expected payout (1 - price)
      const profitFee = (1.0 - price) * contracts * 0.10;
      const withdrawalFee = (1.0 - price) * contracts * 0.05;
      return profitFee + withdrawalFee;

    } else if (platformLower.includes("ibkr") || platformLower.includes("forecastex")) {
      // IBKR ForecastEx 2026: $0.01 fixed fee per contract
      return 0.01 * contracts;
    }
    return 0;
  };

  // Platform-aware ROI calculation for any pair of platforms
  // Returns per-contract values for fees and totalCost
  const calculateScenarioROI = (
    platform1: string,
    price1: number, // Price on platform 1
    platform2: string,
    price2: number, // Price on platform 2
    investment: number,
    mode: "Maker" | "Taker"
  ) => {
    const baseCost = price1 + price2;
    if (baseCost >= 1.0) {
      return { netProfit: 0, roi: 0, fee1: 0, fee2: 0, totalCost: baseCost, isProfitable: false };
    }
    if (investment <= 0) {
      return { netProfit: 0, roi: 0, fee1: 0, fee2: 0, totalCost: baseCost, isProfitable: false };
    }
    
    // Calculate per-contract fees (for 1 contract)
    const fee1PerContract = calculatePlatformFee(platform1, price1, 1, mode);
    const fee2PerContract = calculatePlatformFee(platform2, price2, 1, mode);
    const totalCostPerContract = baseCost + fee1PerContract + fee2PerContract;
    
    if (totalCostPerContract >= 1.0) {
      return { netProfit: 0, roi: 0, fee1: fee1PerContract, fee2: fee2PerContract, totalCost: totalCostPerContract, isProfitable: false };
    }
    
    // Calculate ROI based on per-contract math
    const profitPerContract = 1.0 - totalCostPerContract;
    const roi = (profitPerContract / totalCostPerContract) * 100;
    
    return { 
      netProfit: profitPerContract, 
      roi, 
      fee1: fee1PerContract, 
      fee2: fee2PerContract, 
      totalCost: totalCostPerContract, 
      isProfitable: profitPerContract > 0 
    };
  };

  // Helper to calculate results for a pair of sites
  const calculatePairResults = (
    site1Name: string,
    site1YesPrice: number,
    site2Name: string,
    site2YesPrice: number,
    investmentAmount: number,
    pairLabel: string
  ): ArbitrageResult[] => {
    // Scenario 1: Buy YES on Site 1 + Buy NO on Site 2
    const scenario1 = calculateScenarioROI(site1Name, site1YesPrice, site2Name, 1 - site2YesPrice, investmentAmount, orderMode);
    // Scenario 2: Buy NO on Site 1 + Buy YES on Site 2
    const scenario2 = calculateScenarioROI(site1Name, 1 - site1YesPrice, site2Name, site2YesPrice, investmentAmount, orderMode);

    // Also calculate gross (Maker) values for comparison
    const scenario1Gross = calculateScenarioROI(site1Name, site1YesPrice, site2Name, 1 - site2YesPrice, investmentAmount, "Maker");
    const scenario2Gross = calculateScenarioROI(site1Name, 1 - site1YesPrice, site2Name, site2YesPrice, investmentAmount, "Maker");

    // Calculate prices in cents for display
    const price1 = site1YesPrice * 100;
    const price2 = site2YesPrice * 100;
    const site2NoPrice = 100 - price2;
    const site1NoPrice = 100 - price1;

    // Calculate shares based on actual cost
    const shares1 = scenario1.totalCost < 1 ? Math.floor(investmentAmount / scenario1.totalCost) : 0;
    const shares2 = scenario2.totalCost < 1 ? Math.floor(investmentAmount / scenario2.totalCost) : 0;

    const actualInvestment1 = shares1 * scenario1.totalCost;
    const actualInvestment2 = shares2 * scenario2.totalCost;

    const totalNetProfit1 = shares1 * (1 - scenario1.totalCost);
    const totalNetProfit2 = shares2 * (1 - scenario2.totalCost);

    const totalGrossProfit1 = shares1 * (1 - scenario1Gross.totalCost);
    const totalGrossProfit2 = shares2 * (1 - scenario2Gross.totalCost);

    const totalFees1 = shares1 * (scenario1.fee1 + scenario1.fee2);
    const totalFees2 = shares2 * (scenario2.fee1 + scenario2.fee2);

    // Cost and profit per share in cents
    const costScenario1 = scenario1.totalCost * 100;
    const costScenario2 = scenario2.totalCost * 100;
    const grossProfitScenario1 = (1 - scenario1Gross.totalCost) * 100;
    const grossProfitScenario2 = (1 - scenario2Gross.totalCost) * 100;
    const feePerShare1 = (scenario1.fee1 + scenario1.fee2) * 100;
    const feePerShare2 = (scenario2.fee1 + scenario2.fee2) * 100;
    const netProfitPerShare1 = (1 - scenario1.totalCost) * 100;
    const netProfitPerShare2 = (1 - scenario2.totalCost) * 100;

    // ROI calculations
    const grossRoi1 = actualInvestment1 > 0 ? (totalGrossProfit1 / actualInvestment1) * 100 : 0;
    const grossRoi2 = actualInvestment2 > 0 ? (totalGrossProfit2 / actualInvestment2) * 100 : 0;
    const netRoi1 = actualInvestment1 > 0 ? (totalNetProfit1 / actualInvestment1) * 100 : 0;
    const netRoi2 = actualInvestment2 > 0 ? (totalNetProfit2 / actualInvestment2) * 100 : 0;

    return [
      {
        scenario: `${pairLabel} - Scenario 1`,
        pairLabel,
        action1: `Buy YES on ${site1Name}`,
        action2: `Buy NO on ${site2Name}`,
        price1,
        price2: site2NoPrice,
        cost: costScenario1,
        grossProfit: grossProfitScenario1,
        fees: feePerShare1,
        netProfit: netProfitPerShare1,
        grossRoi: grossRoi1,
        netRoi: netRoi1,
        isProfitable: totalNetProfit1 > 0,
        shares: shares1,
        totalInvestment: actualInvestment1,
        totalGrossProfit: totalGrossProfit1,
        totalFees: totalFees1,
        totalNetProfit: totalNetProfit1,
        siteAName: site1Name,
        siteBName: site2Name,
        siteAYesPrice: site1YesPrice,
        siteBYesPrice: site2YesPrice,
        viewLink: `https://view-market.example.com/${site1Name.toLowerCase()}/${site1YesPrice}` // Simplified example
      },
      {
        scenario: `${pairLabel} - Scenario 2`,
        pairLabel,
        action1: `Buy NO on ${site1Name}`,
        action2: `Buy YES on ${site2Name}`,
        price1: site1NoPrice,
        price2,
        cost: costScenario2,
        grossProfit: grossProfitScenario2,
        fees: feePerShare2,
        netProfit: netProfitPerShare2,
        grossRoi: grossRoi2,
        netRoi: netRoi2,
        isProfitable: totalNetProfit2 > 0,
        shares: shares2,
        totalInvestment: actualInvestment2,
        totalGrossProfit: totalGrossProfit2,
        totalFees: totalFees2,
        totalNetProfit: totalNetProfit2,
        siteAName: site1Name,
        siteBName: site2Name,
        siteAYesPrice: site1YesPrice,
        siteBYesPrice: site2YesPrice,
      }
    ];
  };

  const results = useMemo((): ArbitrageResult[] | null => {
    const investmentAmount = parseFloat(investment) || 100;
    const allResults: ArbitrageResult[] = [];

    // Parse and normalize prices
    const parsePrice = (priceStr: string): number | null => {
      const p = parseFloat(priceStr);
      if (isNaN(p) || p <= 0) return null;
      return p >= 1 ? p / 100 : p;
    };

    const priceA = parsePrice(siteAYes);
    const priceB = parsePrice(siteBYes);
    const priceC = parsePrice(siteCYes);
    const priceD = parsePrice(siteDYes);

    // Count how many valid prices we have
    const validPrices = [priceA, priceB, priceC, priceD].filter(p => p !== null && p < 1);
    if (validPrices.length < 2) return null;

    // Calculate results for each valid pair (A-B, A-C, A-D, B-C, B-D, C-D)
    if (priceA !== null && priceA < 1 && priceB !== null && priceB < 1) {
      allResults.push(...calculatePairResults(siteAName, priceA, siteBName, priceB, investmentAmount, "A-B"));
    }
    if (priceA !== null && priceA < 1 && priceC !== null && priceC < 1) {
      allResults.push(...calculatePairResults(siteAName, priceA, siteCName, priceC, investmentAmount, "A-C"));
    }
    if (priceA !== null && priceA < 1 && priceD !== null && priceD < 1) {
      allResults.push(...calculatePairResults(siteAName, priceA, siteDName, priceD, investmentAmount, "A-D"));
    }
    if (priceB !== null && priceB < 1 && priceC !== null && priceC < 1) {
      allResults.push(...calculatePairResults(siteBName, priceB, siteCName, priceC, investmentAmount, "B-C"));
    }
    if (priceB !== null && priceB < 1 && priceD !== null && priceD < 1) {
      allResults.push(...calculatePairResults(siteBName, priceB, siteDName, priceD, investmentAmount, "B-D"));
    }
    if (priceC !== null && priceC < 1 && priceD !== null && priceD < 1) {
      allResults.push(...calculatePairResults(siteCName, priceC, siteDName, priceD, investmentAmount, "C-D"));
    }

    // Sort by ROI descending to show best opportunities first
    allResults.sort((a, b) => b.netRoi - a.netRoi);

    return allResults.length > 0 ? allResults : null;
  }, [siteAName, siteBName, siteCName, siteDName, siteAYes, siteBYes, siteCYes, siteDYes, investment, orderMode]);

  const saveHistoryMutation = useMutation({
    mutationFn: async (data: InsertArbitrageHistory) => {
      return apiRequest('POST', '/api/arbitrage-history', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['/api/arbitrage-history'] });
      toast({
        title: "Saved to History",
        description: "This arbitrage opportunity has been saved.",
      });
    },
    onError: () => {
      toast({
        title: "Error",
        description: "Failed to save to history.",
        variant: "destructive"
      });
    }
  });

  const handleReset = () => {
    setSiteAName("Kalshi");
    setSiteBName("Polymarket");
    setSiteCName("PredictIt");
    setSiteDName("IBKR ForecastEx");
    setSiteAYes("");
    setSiteBYes("");
    setSiteCYes("");
    setSiteDYes("");
    setInvestment("100");
    setOrderMode("Maker");
    setCopiedScenario(null);
    setSavedScenario(null);
  };

  const handleCopyExecution = (result: ArbitrageResult, index: number) => {
    const feeText = result.totalFees > 0 ? `\nFees: $${result.totalFees.toFixed(2)}` : "";
    const text = `${result.action1}: ${result.shares} shares @ ${(result.price1 / 100).toFixed(2)}\n${result.action2}: ${result.shares} shares @ ${(result.price2 / 100).toFixed(2)}\nTotal: $${result.totalInvestment.toFixed(2)}${feeText} → Net Profit: $${result.totalNetProfit.toFixed(2)} (${orderMode} mode)`;
    navigator.clipboard.writeText(text);
    setCopiedScenario(index);
    setTimeout(() => setCopiedScenario(null), 2000);
  };

  const handleSaveToHistory = (result: ArbitrageResult, index: number) => {
    const investmentAmount = parseFloat(investment) || 100;

    saveHistoryMutation.mutate({
      siteAName: result.siteAName,
      siteBName: result.siteBName,
      siteAYesPrice: result.siteAYesPrice,
      siteBYesPrice: result.siteBYesPrice,
      investment: investmentAmount,
      orderMode,
      isProfitable: result.isProfitable,
      netProfit: result.totalNetProfit,
      netRoi: result.netRoi,
      shares: result.shares,
      scenario: result.scenario,
      marketName: `Manual Calc: ${result.scenario}`
    });

    setSavedScenario(index);
    setTimeout(() => setSavedScenario(null), 2000);
  };

  // Need at least 2 sites filled in
  const filledSites = [siteAYes, siteBYes, siteCYes, siteDYes].filter(p => p !== "").length;
  const hasValidInput = filledSites >= 2;
  const hasPartialInput = filledSites >= 1;

  return (
    <div className="min-h-full bg-background">
      <div className="reveal-stack max-w-4xl mx-auto px-4 py-8 md:py-12">
        <div className="mb-8">
          <PageHeader
            index="06"
            kicker="MODULE // MANUAL CALCULATOR"
            title="Arbitrage Hunter"
            description="Compare identical questions across platforms to find guaranteed-profit spreads"
            icon={Calculator}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Badge variant="secondary" className="text-xs font-medium">A</Badge>
                Site A
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="siteAName" className="text-xs text-muted-foreground">
                  Platform
                </Label>
                <Select value={siteAName} onValueChange={setSiteAName}>
                  <SelectTrigger data-testid="select-site-a-name">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="siteAYes" className="text-xs text-muted-foreground">
                  YES Price
                </Label>
                <Input
                  id="siteAYes"
                  data-testid="input-site-a-yes"
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={siteAYes}
                  onChange={(e) => setSiteAYes(e.target.value)}
                  placeholder="e.g. 52 or 0.52"
                  className="font-mono"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Badge variant="secondary" className="text-xs font-medium">B</Badge>
                Site B
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="siteBName" className="text-xs text-muted-foreground">
                  Platform
                </Label>
                <Select value={siteBName} onValueChange={setSiteBName}>
                  <SelectTrigger data-testid="select-site-b-name">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="siteBYes" className="text-xs text-muted-foreground">
                  YES Price
                </Label>
                <Input
                  id="siteBYes"
                  data-testid="input-site-b-yes"
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={siteBYes}
                  onChange={(e) => setSiteBYes(e.target.value)}
                  placeholder="e.g. 44 or 0.44"
                  className="font-mono"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Badge variant="secondary" className="text-xs font-medium">C</Badge>
                Site C
                <Badge variant="outline" className="text-xs ml-auto">Optional</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="siteCName" className="text-xs text-muted-foreground">
                  Platform
                </Label>
                <Select value={siteCName} onValueChange={setSiteCName}>
                  <SelectTrigger data-testid="select-site-c-name">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="siteCYes" className="text-xs text-muted-foreground">
                  YES Price
                </Label>
                <Input
                  id="siteCYes"
                  data-testid="input-site-c-yes"
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={siteCYes}
                  onChange={(e) => setSiteCYes(e.target.value)}
                  placeholder="e.g. 38 or 0.38"
                  className="font-mono"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Badge variant="secondary" className="text-xs font-medium">D</Badge>
                Site D
                <Badge variant="outline" className="text-xs ml-auto">Optional</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="siteDName" className="text-xs text-muted-foreground">
                  Platform
                </Label>
                <Select value={siteDName} onValueChange={setSiteDName}>
                  <SelectTrigger data-testid="select-site-d-name">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="siteDYes" className="text-xs text-muted-foreground">
                  YES Price
                </Label>
                <Input
                  id="siteDYes"
                  data-testid="input-site-d-yes"
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={siteDYes}
                  onChange={(e) => setSiteDYes(e.target.value)}
                  placeholder="e.g. 29 or 0.29"
                  className="font-mono"
                />
              </div>
            </CardContent>
          </Card>
        </div>

        <p className="text-xs text-muted-foreground text-center mb-4">
          Fill in at least 2 sites to calculate. All 4 sites will compare every pair (A-B, A-C, A-D, B-C, B-D, C-D).
        </p>

        <Card className="mb-6">
          <CardContent className="py-4">
            <div className="flex flex-col gap-4">
              <div className="flex flex-col sm:flex-row sm:items-center gap-4">
                <div className="flex-1">
                  <Label htmlFor="investment" className="text-sm text-muted-foreground">
                    Investment Amount
                  </Label>
                  <div className="relative mt-1">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground font-mono">
                      $
                    </span>
                    <Input
                      id="investment"
                      data-testid="input-investment"
                      type="number"
                      min="1"
                      value={investment}
                      onChange={(e) => setInvestment(e.target.value)}
                      placeholder="100"
                      className="font-mono text-lg pl-8"
                    />
                  </div>
                </div>
                <div className="flex-1">
                  <Label className="text-sm text-muted-foreground">Order Type</Label>
                  <div className="flex items-center gap-3 mt-2">
                    <button
                      onClick={() => setOrderMode("Maker")}
                      className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                        orderMode === "Maker"
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground hover-elevate"
                      }`}
                      data-testid="button-mode-maker"
                    >
                      <Shield className="w-4 h-4" />
                      Maker (0% fee)
                    </button>
                    <button
                      onClick={() => setOrderMode("Taker")}
                      className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                        orderMode === "Taker"
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground hover-elevate"
                      }`}
                      data-testid="button-mode-taker"
                    >
                      <Zap className="w-4 h-4" />
                      Taker (2% fee)
                    </button>
                  </div>
                </div>
              </div>
              <div className="flex items-start gap-2 p-3 rounded-lg bg-muted/50">
                <Info className="w-4 h-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                <p className="text-sm text-muted-foreground">
                  {orderMode === "Maker" 
                    ? "Maker orders (Limit Orders) have 0% fees on Kalshi and Polymarket. Best for patient traders."
                    : "Taker orders (Market Orders) have a 2% fee on Kalshi profits. Faster execution but slightly lower returns."
                  }
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {hasPartialInput && (
          <div className="flex justify-between items-center mb-6 gap-2 flex-wrap">
            <div className="flex gap-2">
              <Link href="/history">
                <Button 
                  variant="outline"
                  size="sm"
                  data-testid="button-view-history"
                >
                  <History className="w-4 h-4 mr-2" />
                  History
                </Button>
              </Link>
              <Link href="/sentinel">
                <Button 
                  variant="outline"
                  size="sm"
                  data-testid="button-view-sentinel"
                >
                  <Eye className="w-4 h-4 mr-2" />
                  Sentinel
                </Button>
              </Link>
            </div>
            <Button 
              onClick={handleReset}
              variant="outline"
              size="sm"
              data-testid="button-reset"
            >
              <RefreshCw className="w-4 h-4 mr-2" />
              Reset
            </Button>
          </div>
        )}

        {showPredictItWarning && (
          <Alert variant="destructive" className="mb-6">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>PredictIt Warning</AlertTitle>
            <AlertDescription>
              PredictIt charges 10% fees on profits and 5% on withdrawals. Both fees are factored into all ROI calculations.
            </AlertDescription>
          </Alert>
        )}

        {hasValidInput && results && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
              {results.map((result, index) => (
                <Card 
                  key={index} 
                  className={`relative overflow-visible ${
                    result.isProfitable 
                      ? "ring-2 ring-green-500/20 dark:ring-green-400/20" 
                      : "ring-2 ring-red-500/20 dark:ring-red-400/20"
                  }`}
                  data-testid={`card-scenario-${index + 1}`}
                >
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <CardTitle className="text-base font-semibold">
                        {result.scenario}
                      </CardTitle>
                      {result.isProfitable ? (
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
                    </div>
                    <div className="text-sm text-muted-foreground space-y-1 mt-2">
                      <p>{result.action1}</p>
                      <p>{result.action2}</p>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex items-center justify-between py-2 border-b border-border">
                      <span className="text-sm text-muted-foreground">Cost per $1 Payout</span>
                      <span className="font-mono font-semibold text-lg" data-testid={`text-cost-${index + 1}`}>
                        ${(result.cost / 100).toFixed(3)}
                      </span>
                    </div>
                    
                    <div className="flex items-center justify-between py-2">
                      <span className="text-sm text-muted-foreground flex items-center gap-1">
                        {result.isProfitable ? (
                          <TrendingUp className="w-4 h-4 text-green-500" />
                        ) : (
                          <TrendingDown className="w-4 h-4 text-red-500" />
                        )}
                        {orderMode === "Taker" ? "Net Margin" : "Margin"}
                      </span>
                      <span
                        className={`font-mono font-bold text-2xl tabular-nums ${
                          result.isProfitable ? "value-pos" : "value-neg"
                        }`}
                        data-testid={`text-margin-${index + 1}`}
                      >
                        {result.netProfit > 0 ? "+" : ""}{(result.netProfit / 100).toFixed(3)}
                      </span>
                    </div>

                    {orderMode === "Taker" && result.totalFees > 0 && (
                      <div className="flex items-center justify-between py-2 text-amber-600 dark:text-amber-400">
                        <span className="text-sm flex items-center gap-1">
                          <DollarSign className="w-4 h-4" />
                          Est. Kalshi Fees
                        </span>
                        <span className="font-mono font-semibold" data-testid={`text-fees-${index + 1}`}>
                          -${result.totalFees.toFixed(2)}
                        </span>
                      </div>
                    )}

                    <div className="flex items-center justify-between py-2 border-t border-border">
                      <span className="text-sm text-muted-foreground flex items-center gap-1">
                        <Percent className="w-4 h-4" />
                        {orderMode === "Taker" ? "Net ROI" : "ROI"}
                      </span>
                      <span 
                        className={`font-mono font-bold text-xl ${
                          result.isProfitable 
                            ? "text-green-600 dark:text-green-400" 
                            : "text-red-600 dark:text-red-400"
                        }`}
                        data-testid={`text-roi-${index + 1}`}
                      >
                        {result.netRoi.toFixed(2)}%
                      </span>
                    </div>

                    <div className={`rounded-lg p-4 border ${
                      result.isProfitable 
                        ? "bg-green-500/5 dark:bg-green-400/5 border-green-500/10" 
                        : "bg-red-500/5 dark:bg-red-400/5 border-red-500/10"
                    }`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-start gap-2">
                          <ShoppingCart className={`w-5 h-5 flex-shrink-0 mt-0.5 ${
                            result.isProfitable 
                              ? "text-green-600 dark:text-green-400" 
                              : "text-red-600 dark:text-red-400"
                          }`} />
                          <div>
                            <p className={`text-sm font-medium ${
                              result.isProfitable 
                                ? "text-green-700 dark:text-green-300" 
                                : "text-red-700 dark:text-red-300"
                            }`}>
                              Execution Plan ({orderMode})
                            </p>
                            <div className="mt-2 space-y-1">
                              <p className="text-sm" data-testid={`text-order-1-${index + 1}`}>
                                <span className="text-muted-foreground">{result.action1}:</span>{" "}
                                <span className="font-mono font-semibold">{result.shares}</span> shares @ <span className="font-mono">${(result.price1 / 100).toFixed(2)}</span>
                              </p>
                              <p className="text-sm" data-testid={`text-order-2-${index + 1}`}>
                                <span className="text-muted-foreground">{result.action2}:</span>{" "}
                                <span className="font-mono font-semibold">{result.shares}</span> shares @ <span className="font-mono">${(result.price2 / 100).toFixed(2)}</span>
                              </p>
                            </div>
                            <div className={`mt-3 pt-2 border-t ${
                              result.isProfitable ? "border-green-500/20" : "border-red-500/20"
                            }`}>
                              <p className={`text-sm ${
                                result.isProfitable 
                                  ? "text-green-600 dark:text-green-400" 
                                  : "text-red-600 dark:text-red-400"
                              }`}>
                                Total: <span className="font-mono font-semibold" data-testid={`text-total-investment-${index + 1}`}>${result.totalInvestment.toFixed(2)}</span>
                                {" → "}
                                <span className="font-mono font-bold" data-testid={`text-total-profit-${index + 1}`}>
                                  {result.totalNetProfit > 0 ? "+" : ""}${result.totalNetProfit.toFixed(2)}
                                </span> net profit
                              </p>
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-col gap-1">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-11 min-h-[44px]"
                            onClick={() => openSideBySide(
                              getPlatformUrl(result.siteAName),
                              getPlatformUrl(result.siteBName)
                            )}
                            data-testid={`button-view-markets-${index + 1}`}
                          >
                            <Columns className="w-4 h-4 mr-1" />
                            View
                            <ExternalLink className="w-3 h-3 ml-1" />
                          </Button>
                          {result.isProfitable && (
                            <>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="min-h-[44px] min-w-[44px]"
                                onClick={() => handleCopyExecution(result, index)}
                                data-testid={`button-copy-${index + 1}`}
                              >
                                {copiedScenario === index ? (
                                  <Check className="w-4 h-4 text-green-500" />
                                ) : (
                                  <Copy className="w-4 h-4" />
                                )}
                              </Button>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="min-h-[44px] min-w-[44px]"
                                onClick={() => handleSaveToHistory(result, index)}
                                disabled={saveHistoryMutation.isPending}
                                data-testid={`button-save-${index + 1}`}
                              >
                                {savedScenario === index ? (
                                  <Check className="w-4 h-4 text-green-500" />
                                ) : (
                                  <Save className="w-4 h-4" />
                                )}
                              </Button>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    {result.isProfitable && (
                      <div className="rounded-lg p-4 border border-blue-500/20 bg-blue-500/5 dark:bg-blue-400/5">
                        <div className="flex items-start gap-2">
                          <Shield className="w-5 h-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
                          <div>
                            <p className="text-sm font-medium text-blue-700 dark:text-blue-300">
                              Strategic Reassurance
                            </p>
                            <div className="mt-2 space-y-2 text-sm text-blue-600 dark:text-blue-400">
                              {orderMode === "Maker" ? (
                                <p>
                                  You are using Limit Orders. The exchange rewards your patience with $0.00 fees, keeping every cent of the arb gap.
                                </p>
                              ) : (
                                <p>
                                  Even after the 2% Taker fee, the price gap is so wide that the math FORCES a profit regardless of the outcome.
                                </p>
                              )}
                              <p>
                                <strong>Logic:</strong> You spent ${(result.cost / 100).toFixed(3)} to buy a guaranteed $1.00.
                              </p>
                              <p>
                                <strong>Safety:</strong> This is Delta-Neutral. You are not betting on the event; you are betting on the two platforms being out of sync.
                              </p>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </>
        )}

        {!hasValidInput && (
          <Card className="border-dashed">
            <CardContent className="py-12 text-center">
              <div className="flex flex-col items-center gap-3">
                <div className="p-3 rounded-full bg-muted">
                  <Calculator className="w-6 h-6 text-muted-foreground" />
                </div>
                <div>
                  <p className="text-muted-foreground font-medium">
                    Enter prices to calculate
                  </p>
                  <p className="text-sm text-muted-foreground/70">
                    Input the YES prices (as decimals like 0.52) from both platforms to see arbitrage opportunities
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
