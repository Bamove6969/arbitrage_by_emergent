import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { 
  ArrowLeft, 
  Plus, 
  Trash2, 
  Bell, 
  BellRing, 
  BellOff,
  Eye, 
  EyeOff,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Flame,
  Calculator,
  Volume2,
  VolumeX,
  Clock,
  Check,
  Square,
  Radar
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// Available platforms with their fee structures (2026 Updated)
const PLATFORMS = [
  { id: "kalshi", name: "Kalshi", feeType: "Variable (Maker/Taker)", description: "0.0175% to 0.07% based on price" },
  { id: "polymarket", name: "Polymarket", feeType: "0% Commission", description: "Standard markets are free" },
  { id: "predictit", name: "PredictIt", feeType: "Profit & Withdraw Fees", description: "10% on profit, 5% on withdrawal" },
  { id: "ibkr", name: "IBKR Forecast", feeType: "$0.01 / contract", description: "Fixed pricing ($1 per 100 shares)" },
];

// Preset markets - popular questions across platforms (prices fetched live)
const PRESET_MARKETS = [
  { id: "fed-rate-cut", name: "Fed Rate Cut This Year", siteA: "Kalshi", siteB: "Polymarket" },
  { id: "recession-2026", name: "US Recession in 2026", siteA: "Kalshi", siteB: "Polymarket" },
  { id: "trump-approval", name: "Trump Approval Rating Above 50%", siteA: "Kalshi", siteB: "PredictIt" },
  { id: "btc-100k", name: "Bitcoin Above $100K by Year End", siteA: "Kalshi", siteB: "Polymarket" },
];
import { apiRequest } from "@/lib/queryClient";
import { useToast } from "@/hooks/use-toast";
import type { Watchlist, Alert } from "@shared/schema";
import { 
  requestNotificationPermission, 
  isNotificationEnabled, 
  triggerUrgentAlert,
  playAlertSound,
  setGlobalVolume,
  getGlobalVolume,
  sendUrgentNotification,
  setCustomSound,
  clearCustomSound,
  hasCustomSound,
  getCustomSoundName,
} from "@/lib/notifications";
import { MarketBrowser } from "@/components/market-browser";
import { Slider } from "@/components/ui/slider";

// Calculate fee for a specific platform (2026 Updated)
function calculatePlatformFee(platform: string, price: number, contracts: number, mode: "Maker" | "Taker" = "Taker"): number {
  const platformLower = platform.toLowerCase();
  
  if (price <= 0 || price >= 1) return 0;

  if (platformLower === "kalshi") {
    const multiplier = mode === "Taker" ? 0.07 : 0.0175;
    const feePerContract = multiplier * price * (1 - price);
    const cap = mode === "Taker" ? 0.0175 : 0.0044;
    return Math.min(cap, feePerContract) * contracts;

  } else if (platformLower === "polymarket") {
    // 0% for standard markets
    return 0;

  } else if (platformLower === "predictit") {
    const profitFee = (1.0 - price) * contracts * 0.10;
    const withdrawalFee = (1.0 - price) * contracts * 0.05;
    return profitFee + withdrawalFee;

  } else if (platformLower === "ibkr" || platformLower.includes("forecastex")) {
    return contracts * 0.01;
  }
  return 0;
}

// Calculate ROI for a single scenario with any two platforms
// Uses integer share counts for accurate fee calculations
function calculateScenarioRoi(
  platformA: string, priceA: number, 
  platformB: string, priceB: number, 
  investment: number, 
  mode: "Maker" | "Taker" = "Taker"
): number {
  // Total cost per pair = priceA + priceB (before fees)
  const baseCost = priceA + priceB;
  if (baseCost >= 1.0) return 0.0;
  if (investment <= 0) return 0;
  
  // Calculate maximum integer contract pairs we can afford
  // We need to account for fees in determining how many we can buy
  let maxContracts = Math.floor(investment / baseCost);
  
  // Iteratively find the max contracts we can afford with fees
  while (maxContracts > 0) {
    const feeA = calculatePlatformFee(platformA, priceA, maxContracts, mode);
    const feeB = calculatePlatformFee(platformB, priceB, maxContracts, mode);
    const totalCost = (baseCost * maxContracts) + feeA + feeB;
    if (totalCost <= investment) break;
    maxContracts--;
  }
  
  if (maxContracts <= 0) return 0;
  
  // Calculate actual costs and payout
  const feeA = calculatePlatformFee(platformA, priceA, maxContracts, mode);
  const feeB = calculatePlatformFee(platformB, priceB, maxContracts, mode);
  const totalInvested = (baseCost * maxContracts) + feeA + feeB;
  const payout = maxContracts; // Each pair pays out $1
  const netProfit = payout - totalInvested;
  
  return (netProfit / totalInvested) * 100;
}

// Calculate best ROI across both scenarios for any two platforms
// priceAYes and priceBYes are both YES prices for each platform
function calculateBestRoi(
  platformA: string, priceAYes: number,
  platformB: string, priceBYes: number,
  investment: number,
  mode: "Maker" | "Taker" = "Taker"
): { roi: number; scenario: number; feeA: number; feeB: number } {
  // Scenario 1: Buy YES on A + Buy NO on B
  const roi1 = calculateScenarioRoi(platformA, priceAYes, platformB, 1 - priceBYes, investment, mode);
  
  // Scenario 2: Buy NO on A + Buy YES on B
  const roi2 = calculateScenarioRoi(platformA, 1 - priceAYes, platformB, priceBYes, investment, mode);
  
  // Calculate fees for display (for the best scenario)
  const bestScenario = roi1 >= roi2 ? 1 : 2;
  const bestPriceA = bestScenario === 1 ? priceAYes : 1 - priceAYes;
  const bestPriceB = bestScenario === 1 ? 1 - priceBYes : priceBYes;
  const baseCost = bestPriceA + bestPriceB;
  
  // Use integer contracts for fee calculation
  let contracts = baseCost < 1 ? Math.floor(investment / baseCost) : 0;
  while (contracts > 0) {
    const fA = calculatePlatformFee(platformA, bestPriceA, contracts, mode);
    const fB = calculatePlatformFee(platformB, bestPriceB, contracts, mode);
    if ((baseCost * contracts) + fA + fB <= investment) break;
    contracts--;
  }
  
  return {
    roi: Math.max(roi1, roi2),
    scenario: bestScenario,
    feeA: calculatePlatformFee(platformA, bestPriceA, contracts, mode),
    feeB: calculatePlatformFee(platformB, bestPriceB, contracts, mode)
  };
}

export default function SentinelPage() {
  const [marketName, setMarketName] = useState("");
  const [siteAName, setSiteAName] = useState("Kalshi");
  const [siteBName, setSiteBName] = useState("Polymarket");
  const [siteAYes, setSiteAYes] = useState("");
  const [siteBYes, setSiteBYes] = useState("");
  const [investment, setInvestment] = useState("500");
  const [alertThreshold, setAlertThreshold] = useState("3");
  
  // Notification settings
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [volume, setVolume] = useState(getGlobalVolume() * 100);
  
  // Unified scan settings
  const [autoScanEnabled, setAutoScanEnabled] = useState(false);
  const [scanInterval, setScanInterval] = useState("5"); // minutes
  const [lastScan, setLastScan] = useState<Date | null>(null);
  
  // Platform toggles for filtering which platforms to scan
  const [enabledPlatforms, setEnabledPlatforms] = useState<string[]>(["Kalshi", "Polymarket", "PredictIt", "IBKR Forecast"]);
  
  // Selected preset markets
  const [selectedPresets, setSelectedPresets] = useState<string[]>([]);
  
  // Toggle platform
  const togglePlatform = (platform: string) => {
    setEnabledPlatforms(prev => {
      if (prev.includes(platform)) {
        // Don't allow disabling all platforms
        if (prev.length <= 1) return prev;
        return prev.filter(p => p !== platform);
      }
      return [...prev, platform];
    });
  };

  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Sync scanner config with backend
  const { data: scannerConfig } = useQuery({
    queryKey: ["/api/scanner-config"],
    queryFn: async () => {
      const res = await fetch("/api/scanner-config");
      return res.json();
    }
  });

  const updateScannerMutation = useMutation({
    mutationFn: async (enabled: boolean) => {
      const res = await apiRequest("POST", "/api/scanner-config", { enabled });
      return res.json();
    },
    onSuccess: (data) => {
      setAutoScanEnabled(data.autoScan);
      queryClient.invalidateQueries({ queryKey: ["/api/scanner-config"] });
    }
  });

  useEffect(() => {
    if (scannerConfig) {
      setAutoScanEnabled(scannerConfig.autoScan);
    }
  }, [scannerConfig]);

  const handleToggleAutoScan = (enabled: boolean) => {
    setAutoScanEnabled(enabled); // Optimistic update
    updateScannerMutation.mutate(enabled);
    if (enabled) {
      toast({ title: "Auto-scan enabled", description: "The backend will now scan markets periodically" });
    } else {
      toast({ title: "Auto-scan disabled" });
    }
  };

  // Check notification permission on mount
  useEffect(() => {
    setNotificationsEnabled(isNotificationEnabled());
  }, []);

  const handleEnableNotifications = async () => {
    const granted = await requestNotificationPermission();
    setNotificationsEnabled(granted);
    if (granted) {
      toast({ title: "Notifications enabled", description: "You'll receive alerts when opportunities are found" });
    } else {
      toast({ title: "Notifications blocked", description: "Please enable notifications in your browser settings", variant: "destructive" });
    }
  };

  const [customSoundName, setCustomSoundName] = useState<string | null>(
    hasCustomSound() ? (getCustomSoundName() || "Custom sound loaded") : null
  );

  const testAlertSound = async () => {
    await playAlertSound(5);
    toast({ title: "Test alert", description: "Playing urgent alert sound" });
  };

  const handleCustomSoundUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await setCustomSound(file);
      setCustomSoundName(file.name);
      toast({ title: "Custom sound set", description: `Using "${file.name}" as alert sound` });
    } catch (err) {
      toast({ title: "Invalid audio file", description: "Please upload a valid .mp3, .wav, or .ogg file", variant: "destructive" });
    }
    e.target.value = "";
  };

  const handleClearCustomSound = () => {
    clearCustomSound();
    setCustomSoundName(null);
    toast({ title: "Custom sound removed", description: "Using default alert sounds" });
  };

  const handleVolumeChange = (values: number[]) => {
    const newVolume = values[0];
    setVolume(newVolume);
    setGlobalVolume(newVolume / 100);
  };

  // Toggle preset market selection
  const togglePreset = (presetId: string) => {
    setSelectedPresets(prev => 
      prev.includes(presetId) 
        ? prev.filter(id => id !== presetId)
        : [...prev, presetId]
    );
  };

  const { data: watchlistItems = [], isLoading: watchlistLoading } = useQuery<Watchlist[]>({
    queryKey: ["/api/watchlist"],
  });

  const { data: alertsList = [], isLoading: alertsLoading } = useQuery<Alert[]>({
    queryKey: ["/api/alerts"],
  });

  const unreadAlerts = useMemo(() => alertsList.filter(a => !a.isRead), [alertsList]);

  const addToWatchlistMutation = useMutation({
    mutationFn: async (data: any) => {
      return apiRequest("POST", "/api/watchlist", data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/watchlist"] });
      toast({ title: "Added to watchlist", description: `${marketName} is now being monitored` });
      setMarketName("");
      setSiteAYes("");
      setSiteBYes("");
    },
    onError: () => {
      toast({ title: "Error", description: "Failed to add to watchlist", variant: "destructive" });
    },
  });

  const deleteWatchlistMutation = useMutation({
    mutationFn: async (id: string) => {
      return apiRequest("DELETE", `/api/watchlist/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/watchlist"] });
      toast({ title: "Removed", description: "Market removed from watchlist" });
    },
  });

  const updateWatchlistMutation = useMutation({
    mutationFn: async ({ id, updates }: { id: string; updates: any }) => {
      return apiRequest("PATCH", `/api/watchlist/${id}`, updates);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/watchlist"] });
    },
  });

  const markAlertReadMutation = useMutation({
    mutationFn: async (id: string) => {
      return apiRequest("PATCH", `/api/alerts/${id}/read`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/alerts"] });
    },
  });

  const clearAlertsMutation = useMutation({
    mutationFn: async () => {
      return apiRequest("DELETE", "/api/alerts");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/alerts"] });
      toast({ title: "Alerts cleared" });
    },
  });

  const createAlertMutation = useMutation({
    mutationFn: async (data: any) => {
      return apiRequest("POST", "/api/alerts", data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/alerts"] });
    },
  });

  const handleAddToWatchlist = () => {
    let priceA = parseFloat(siteAYes);
    let priceB = parseFloat(siteBYes);

    if (isNaN(priceA) || isNaN(priceB) || !marketName.trim()) {
      toast({ title: "Invalid input", description: "Please fill in all fields", variant: "destructive" });
      return;
    }

    if (priceA >= 1) priceA = priceA / 100;
    if (priceB >= 1) priceB = priceB / 100;

    const makerResult = calculateBestRoi(siteAName, priceA, siteBName, priceB, parseFloat(investment) || 500, "Maker");
    const takerResult = calculateBestRoi(siteAName, priceA, siteBName, priceB, parseFloat(investment) || 500, "Taker");

    addToWatchlistMutation.mutate({
      marketName: marketName.trim(),
      siteAName,
      siteBName,
      siteAYesPrice: priceA,
      siteBYesPrice: priceB,
      investment: parseFloat(investment) || 500,
      alertThreshold: parseFloat(alertThreshold) || 3.0,
      isActive: true,
    });
  };

  const handleScanMarket = (item: Watchlist) => {
    const makerResult = calculateBestRoi(item.siteAName, item.siteAYesPrice, item.siteBName, item.siteBYesPrice, item.investment, "Maker");
    const takerResult = calculateBestRoi(item.siteAName, item.siteAYesPrice, item.siteBName, item.siteBYesPrice, item.investment, "Taker");

    updateWatchlistMutation.mutate({
      id: item.id,
      updates: {
        lastChecked: new Date().toISOString(),
        lastMakerRoi: makerResult.roi,
        lastTakerRoi: takerResult.roi,
      },
    });

    if (makerResult.roi >= item.alertThreshold) {
      const scenarioDesc = makerResult.scenario === 1 
        ? `Buy YES on ${item.siteAName} + Buy NO on ${item.siteBName}`
        : `Buy NO on ${item.siteAName} + Buy YES on ${item.siteBName}`;
      createAlertMutation.mutate({
        watchlistId: item.id,
        marketName: item.marketName,
        makerRoi: makerResult.roi,
        takerRoi: takerResult.roi,
        siteAYesPrice: item.siteAYesPrice,
        siteBYesPrice: item.siteBYesPrice,
        isRead: false,
      });

      // Trigger urgent alert with sound, vibration, flash, and notification
      playAlertSound(makerResult.roi);
      
      if (notificationsEnabled) {
        sendUrgentNotification(
          "Arbitrage Opportunity Found!",
          `${item.marketName}: ${makerResult.roi.toFixed(2)}% ROI`
        );
      }

      toast({
        title: "Arbitrage Opportunity Found!",
        description: `${item.marketName}: ${makerResult.roi.toFixed(2)}% ROI (Scenario ${makerResult.scenario})`,
      });
    }
  };

  const scanAllMarkets = () => {
    watchlistItems.forEach((item) => {
      if (item.isActive) {
        handleScanMarket(item);
      }
    });
    setLastScan(new Date());
    toast({ title: "Scan complete", description: `Checked ${watchlistItems.filter(i => i.isActive).length} markets` });
  };

  // Watchlist auto-scan effect - periodically scans watchlist items when enabled
  useEffect(() => {
    if (!autoScanEnabled || watchlistItems.length === 0) return;
    
    const intervalMinutes = parseInt(scanInterval);
    // Validate interval - must be a positive number, minimum 1 minute
    if (isNaN(intervalMinutes) || intervalMinutes < 1) return;
    
    const intervalMs = intervalMinutes * 60 * 1000;
    const interval = setInterval(() => {
      watchlistItems.forEach((item) => {
        if (item.isActive) {
          handleScanMarket(item);
        }
      });
      setLastScan(new Date());
    }, intervalMs);
    
    return () => clearInterval(interval);
  }, [autoScanEnabled, scanInterval, watchlistItems]);

  // Add selected preset markets to watchlist
  const addSelectedPresets = () => {
    const presetsToAdd = PRESET_MARKETS.filter(p => selectedPresets.includes(p.id));
    if (presetsToAdd.length === 0) {
      toast({ title: "No markets selected", description: "Check the markets you want to add", variant: "destructive" });
      return;
    }
    
    presetsToAdd.forEach(preset => {
      addToWatchlistMutation.mutate({
        marketName: preset.name,
        siteAName: preset.siteA,
        siteBName: preset.siteB,
        siteAYesPrice: 0.5, // Default placeholder
        siteBYesPrice: 0.5, // Default placeholder
        investment: parseFloat(investment) || 500,
        alertThreshold: parseFloat(alertThreshold) || 3,
        isActive: true,
      });
    });
    
    setSelectedPresets([]);
    toast({ title: "Markets added", description: `Added ${presetsToAdd.length} markets to watchlist` });
  };

  return (
    <div className="container mx-auto px-4 py-6">
      <div className="flex items-center justify-between gap-4 mb-6 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Radar className="w-6 h-6 text-primary" />
            Arbitrage Radar
          </h1>
          <p className="text-sm text-muted-foreground font-mono text-xs uppercase tracking-widest">Live cross-platform scanner · watchlist · alerts</p>
        </div>
        {unreadAlerts.length > 0 && (
          <Badge variant="destructive" className="animate-pulse" data-testid="badge-unread-alerts">
            {unreadAlerts.length} alerts
          </Badge>
        )}
      </div>

        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <RefreshCw className="w-5 h-5" />
              Scan Settings
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex-1">
                <div className="font-medium">Auto-Scan Markets</div>
                <p className="text-sm text-muted-foreground">
                  {autoScanEnabled 
                    ? `Scanning every ${scanInterval} minute${scanInterval !== "1" ? "s" : ""}`
                    : "Enable to automatically find arbitrage opportunities"
                  }
                </p>
                {lastScan && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Last scan: {lastScan.toLocaleTimeString()}
                  </p>
                )}
              </div>
              <Switch 
                checked={autoScanEnabled} 
                onCheckedChange={handleToggleAutoScan}
                data-testid="switch-auto-scan"
                disabled={updateScannerMutation.isPending}
              />
            </div>
            {autoScanEnabled && (
              <div className="flex items-center gap-4">
                <Label htmlFor="scan-interval" className="whitespace-nowrap">Scan every:</Label>
                <Select value={scanInterval} onValueChange={setScanInterval}>
                  <SelectTrigger className="w-32" data-testid="select-scan-interval">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="5">5 minutes</SelectItem>
                    <SelectItem value="10">10 minutes</SelectItem>
                    <SelectItem value="15">15 minutes</SelectItem>
                    <SelectItem value="30">30 minutes</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="border-t pt-4">
              <div className="font-medium mb-2">Platforms to Scan</div>
              <p className="text-xs text-muted-foreground mb-3">
                Toggle which platforms to include in arbitrage scanning
              </p>
              <div className="flex gap-3 flex-wrap">
                {PLATFORMS.map((platform) => (
                  <Button
                    key={platform.id}
                    variant={enabledPlatforms.includes(platform.name) ? "default" : "outline"}
                    size="sm"
                    onClick={() => togglePlatform(platform.name)}
                    data-testid={`toggle-platform-${platform.id}`}
                  >
                    {enabledPlatforms.includes(platform.name) ? (
                      <Check className="w-4 h-4 mr-1" />
                    ) : null}
                    {platform.name}
                  </Button>
                ))}
              </div>
              {enabledPlatforms.length === 2 && (
                <p className="text-xs text-muted-foreground mt-2">
                  Only showing {enabledPlatforms.join(" vs ")} matches
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        <div className="mb-6">
          <MarketBrowser 
            autoRefresh={autoScanEnabled}
            refreshInterval={scanInterval}
            enabledPlatforms={enabledPlatforms}
            onScanComplete={() => setLastScan(new Date())}
            excludeWeather={true}
          />
        </div>

      <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Plus className="w-5 h-5" />
              Add Market to Watchlist
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <Label htmlFor="marketName">Market Name</Label>
                <Input
                  id="marketName"
                  value={marketName}
                  onChange={(e) => setMarketName(e.target.value)}
                  placeholder="e.g. SEC Solana ETF Approval"
                  className="min-h-[44px]"
                  data-testid="input-market-name"
                />
              </div>
              <div>
                <Label htmlFor="siteAName">Platform A</Label>
                <Select value={siteAName} onValueChange={setSiteAName}>
                  <SelectTrigger className="min-h-[44px]" data-testid="select-site-a">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>
                        {p.name} ({p.feeType})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="siteBName">Platform B</Label>
                <Select value={siteBName} onValueChange={setSiteBName}>
                  <SelectTrigger className="min-h-[44px]" data-testid="select-site-b">
                    <SelectValue placeholder="Select platform" />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((p) => (
                      <SelectItem key={p.id} value={p.name}>
                        {p.name} ({p.feeType})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="siteAYes">{siteAName} YES Price</Label>
                <Input
                  id="siteAYes"
                  type="number"
                  value={siteAYes}
                  onChange={(e) => setSiteAYes(e.target.value)}
                  placeholder="e.g. 28 or 0.28"
                  className="min-h-[44px]"
                  data-testid="input-site-a-yes"
                />
              </div>
              <div>
                <Label htmlFor="siteBYes">{siteBName} YES Price</Label>
                <Input
                  id="siteBYes"
                  type="number"
                  value={siteBYes}
                  onChange={(e) => setSiteBYes(e.target.value)}
                  placeholder="e.g. 39 or 0.39"
                  className="min-h-[44px]"
                  data-testid="input-site-b-yes"
                />
              </div>
              <div>
                <Label htmlFor="investment">Investment ($)</Label>
                <Input
                  id="investment"
                  type="number"
                  value={investment}
                  onChange={(e) => setInvestment(e.target.value)}
                  placeholder="500"
                  className="min-h-[44px]"
                  data-testid="input-investment"
                />
              </div>
              <div>
                <Label htmlFor="alertThreshold">Alert Threshold (%)</Label>
                <Input
                  id="alertThreshold"
                  type="number"
                  value={alertThreshold}
                  onChange={(e) => setAlertThreshold(e.target.value)}
                  placeholder="3.0"
                  className="min-h-[44px]"
                  data-testid="input-alert-threshold"
                />
              </div>
            </div>
            <Button 
              onClick={handleAddToWatchlist} 
              disabled={addToWatchlistMutation.isPending}
              className="w-full min-h-[44px]"
              data-testid="button-add-watchlist"
            >
              <Plus className="w-4 h-4 mr-2" />
              Add to Watchlist
            </Button>
          </CardContent>
        </Card>

        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              Supported Platforms
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {PLATFORMS.map((platform) => (
                <div key={platform.id} className="p-3 rounded-md border bg-muted/30" data-testid={`platform-info-${platform.id}`}>
                  <div className="font-medium text-sm">{platform.name}</div>
                  <div className="text-xs text-muted-foreground">{platform.feeType} fees</div>
                  <div className="text-xs text-muted-foreground mt-1">{platform.description}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Check className="w-5 h-5" />
              Quick Add Markets
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">Select markets to add to your watchlist:</p>
            <div className="space-y-3">
              {PRESET_MARKETS.map((preset) => {
                const alreadyAdded = watchlistItems.some(w => w.marketName === preset.name);
                return (
                  <div 
                    key={preset.id} 
                    className={`flex items-center gap-3 p-3 rounded-md border ${alreadyAdded ? 'bg-muted/50 opacity-60' : 'hover-elevate'}`}
                    data-testid={`preset-${preset.id}`}
                  >
                    <Checkbox
                      id={preset.id}
                      checked={selectedPresets.includes(preset.id)}
                      onCheckedChange={() => togglePreset(preset.id)}
                      disabled={alreadyAdded}
                      data-testid={`checkbox-${preset.id}`}
                    />
                    <label 
                      htmlFor={preset.id} 
                      className={`flex-1 cursor-pointer ${alreadyAdded ? 'line-through' : ''}`}
                    >
                      <div className="font-medium">{preset.name}</div>
                      <div className="text-sm text-muted-foreground">{preset.siteA} vs {preset.siteB}</div>
                    </label>
                    {alreadyAdded && <Badge variant="secondary">Added</Badge>}
                  </div>
                );
              })}
            </div>
            <Button 
              onClick={addSelectedPresets}
              disabled={selectedPresets.length === 0 || addToWatchlistMutation.isPending}
              className="w-full"
              data-testid="button-add-selected-presets"
            >
              <Plus className="w-4 h-4 mr-2" />
              Add Selected Markets ({selectedPresets.length})
            </Button>
          </CardContent>
        </Card>

        <Card className="mb-6">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Bell className="w-5 h-5" />
              Alert Settings
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex-1">
                <div className="font-medium flex items-center gap-2">
                  {notificationsEnabled ? <BellRing className="w-4 h-4 text-green-500" /> : <BellOff className="w-4 h-4 text-muted-foreground" />}
                  Push Notifications
                </div>
                <p className="text-sm text-muted-foreground">Get notified when opportunities are found</p>
              </div>
              {notificationsEnabled ? (
                <Badge variant="secondary">Enabled</Badge>
              ) : (
                <Button 
                  variant="outline" 
                  size="sm" 
                  onClick={handleEnableNotifications}
                  data-testid="button-enable-notifications"
                >
                  Enable
                </Button>
              )}
            </div>
            <div className="flex items-center justify-between gap-4">
              <div className="flex-1">
                <div className="font-medium flex items-center gap-2">
                  {soundEnabled ? <Volume2 className="w-4 h-4 text-green-500" /> : <VolumeX className="w-4 h-4 text-muted-foreground" />}
                  Sound Alerts
                </div>
                <p className="text-sm text-muted-foreground">Play urgent sound when opportunities are found</p>
              </div>
              <div className="flex items-center gap-2">
                <Button 
                  variant="ghost" 
                  size="sm" 
                  onClick={() => playAlertSound(5)}
                  data-testid="button-test-sound"
                >
                  Test
                </Button>
                <Switch 
                  checked={soundEnabled} 
                  onCheckedChange={setSoundEnabled}
                  data-testid="switch-sound-enabled"
                />
              </div>
            </div>
            {soundEnabled && (
              <div className="space-y-3 py-2 border-t">
                <div className="flex justify-between items-center">
                  <Label className="text-sm font-medium flex items-center gap-2">
                    <Volume2 className="w-4 h-4" />
                    Volume Control
                  </Label>
                  <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">{volume}%</span>
                </div>
                <Slider 
                  value={[volume]} 
                  onValueChange={handleVolumeChange}
                  max={100} 
                  step={1}
                  className="w-full"
                  data-testid="slider-volume"
                />
                <p className="text-xs text-muted-foreground">
                  ROI-based sounds: Urgent (≥5%), Normal (3-5%), Gentle (1-3%)
                </p>
                <div className="border-t pt-3 space-y-2">
                  <Label className="text-sm font-medium">Custom Alert Sound</Label>
                  <div className="flex items-center gap-2">
                    <label className="cursor-pointer">
                      <input
                        type="file"
                        accept="audio/*"
                        className="hidden"
                        onChange={handleCustomSoundUpload}
                        data-testid="input-custom-sound"
                      />
                      <Button variant="outline" size="sm" asChild>
                        <span>Upload Sound File</span>
                      </Button>
                    </label>
                    {customSoundName && (
                      <>
                        <span className="text-xs text-muted-foreground truncate max-w-[150px]">{customSoundName}</span>
                        <Button variant="ghost" size="sm" onClick={handleClearCustomSound} data-testid="button-clear-sound">
                          <Trash2 className="w-3 h-3" />
                        </Button>
                      </>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {customSoundName ? "Custom sound active — replaces default beeps" : "Upload .mp3, .wav, or .ogg to replace default alert beeps"}
                  </p>
                </div>
              </div>
            )}
            <p className="text-xs text-muted-foreground border-t pt-3">
              Note: Web apps cannot fully override Do Not Disturb mode. For critical alerts, enable notifications and sounds, and add this app to your phone's DND exceptions.
            </p>
          </CardContent>
        </Card>

        <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Eye className="w-5 h-5" />
            Watchlist ({watchlistItems.length})
          </h2>
          <Button 
            onClick={scanAllMarkets} 
            disabled={watchlistItems.length === 0}
            data-testid="button-scan-all"
          >
            <RefreshCw className="w-4 h-4 mr-2" />
            Scan All Markets
          </Button>
        </div>

        {watchlistLoading ? (
          <Card className="p-8 text-center text-muted-foreground">Loading watchlist...</Card>
        ) : watchlistItems.length === 0 ? (
          <Card className="p-8 text-center text-muted-foreground">
            <Eye className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No markets in watchlist</p>
            <p className="text-sm mt-2">Add markets above to start monitoring</p>
          </Card>
        ) : (
          <div className="space-y-3">
            {watchlistItems.map((item) => {
              const makerResult = calculateBestRoi(item.siteAName, item.siteAYesPrice, item.siteBName, item.siteBYesPrice, item.investment, "Maker");
              const takerResult = calculateBestRoi(item.siteAName, item.siteAYesPrice, item.siteBName, item.siteBYesPrice, item.investment, "Taker");
              const makerRoi = item.lastMakerRoi ?? makerResult.roi;
              const takerRoi = item.lastTakerRoi ?? takerResult.roi;
              const isHot = makerRoi >= item.alertThreshold;

              return (
                <Card 
                  key={item.id} 
                  className={`${isHot ? "border-orange-500/50 bg-orange-500/5" : ""}`}
                  data-testid={`card-watchlist-${item.id}`}
                >
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-4 flex-wrap">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <h3 className="font-semibold">{item.marketName}</h3>
                          {isHot && (
                            <Badge variant="destructive" className="flex items-center gap-1">
                              <Flame className="w-3 h-3" />
                              HOT
                            </Badge>
                          )}
                          {item.isActive ? (
                            <Badge variant="secondary" className="text-xs">Active</Badge>
                          ) : (
                            <Badge variant="outline" className="text-xs">Paused</Badge>
                          )}
                        </div>
                        <div className="text-sm text-muted-foreground space-y-1">
                          <p>
                            {item.siteAName} YES: <span className="font-mono">${item.siteAYesPrice.toFixed(2)}</span>
                            {" | "}
                            {item.siteBName} NO: <span className="font-mono">${item.siteBYesPrice.toFixed(2)}</span>
                          </p>
                          <p>
                            Investment: <span className="font-mono">${item.investment}</span>
                            {" | "}
                            Alert at: <span className="font-mono">{item.alertThreshold}%</span>
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-2">
                        <div className="text-right">
                          <div 
                            className={`font-mono font-bold text-lg ${makerRoi > 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
                            data-testid={`text-roi-${item.id}`}
                          >
                            {makerRoi > 0 ? <TrendingUp className="w-4 h-4 inline mr-1" /> : <TrendingDown className="w-4 h-4 inline mr-1" />}
                            {makerRoi.toFixed(2)}%
                          </div>
                          <div className="text-xs text-muted-foreground">Maker ROI</div>
                        </div>
                        <div className="flex items-center gap-1">
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => handleScanMarket(item)}
                            data-testid={`button-scan-${item.id}`}
                          >
                            <RefreshCw className="w-4 h-4" />
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => updateWatchlistMutation.mutate({ id: item.id, updates: { isActive: !item.isActive } })}
                            data-testid={`button-toggle-${item.id}`}
                          >
                            {item.isActive ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => deleteWatchlistMutation.mutate(item.id)}
                            data-testid={`button-delete-${item.id}`}
                          >
                            <Trash2 className="w-4 h-4 text-red-500" />
                          </Button>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {alertsList.length > 0 && (
          <div className="mt-8">
            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <h2 className="text-xl font-semibold flex items-center gap-2">
                <BellRing className="w-5 h-5 text-orange-500" />
                Alerts ({alertsList.length})
              </h2>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => clearAlertsMutation.mutate()}
                data-testid="button-clear-alerts"
              >
                Clear All
              </Button>
            </div>
            <div className="space-y-2">
              {alertsList.slice(0, 10).map((alert) => (
                <Card 
                  key={alert.id} 
                  className={`${!alert.isRead ? "border-orange-500/30 bg-orange-500/5" : "opacity-60"}`}
                  data-testid={`card-alert-${alert.id}`}
                >
                  <CardContent className="p-3 flex items-center justify-between gap-4 flex-wrap">
                    <div>
                      <p className="font-medium flex items-center gap-2">
                        {!alert.isRead && <Bell className="w-4 h-4 text-orange-500" />}
                        {alert.marketName}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        Maker: <span className="font-mono text-green-600">{alert.makerRoi.toFixed(2)}%</span>
                        {" | "}
                        Taker: <span className="font-mono">{alert.takerRoi.toFixed(2)}%</span>
                      </p>
                    </div>
                    {!alert.isRead && (
                      <Button 
                        size="sm" 
                        variant="ghost"
                        onClick={() => markAlertReadMutation.mutate(alert.id)}
                        data-testid={`button-mark-read-${alert.id}`}
                      >
                        Mark Read
                      </Button>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
    </div>
  );
}
