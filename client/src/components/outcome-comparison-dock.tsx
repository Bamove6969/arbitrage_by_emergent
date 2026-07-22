import { useComparison } from '@/contexts/comparison-context';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { PlatformTag, platformAccent } from '@/components/terminal';
import { X, ExternalLink, ArrowLeftRight, TrendingUp } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface OutcomeBarProps {
  label: string;
  percentage: number;
  isHighlighted?: boolean;
}

function OutcomeBar({ label, percentage, isHighlighted }: OutcomeBarProps) {
  const displayPercent = Math.round(percentage * 100);

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="truncate flex-1 font-medium">{label}</span>
        <span className={`font-mono font-bold ml-2 tabular-nums ${isHighlighted ? 'value-pos' : ''}`}>
          {displayPercent}%
        </span>
      </div>
      <div className="h-1.5 bg-muted overflow-hidden">
        <motion.div
          className={`h-full ${isHighlighted ? 'bg-chart-1' : 'bg-primary/50'}`}
          initial={{ width: 0 }}
          animate={{ width: `${displayPercent}%` }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
        />
      </div>
    </div>
  );
}

interface MarketPanelProps {
  market: {
    id: string;
    platform: string;
    title: string;
    yesPrice: number;
    noPrice: number;
    marketUrl?: string;
    outcomes?: { label: string; yesPrice: number; noPrice: number }[];
    outcomeCount: number;
  };
  side: 'left' | 'right';
  onClose: () => void;
}

function MarketPanel({ market, side, onClose }: MarketPanelProps) {
  const hasOutcomes = market.outcomes && market.outcomes.length > 0;

  const sortedOutcomes = hasOutcomes
    ? [...market.outcomes!].sort((a, b) => b.yesPrice - a.yesPrice)
    : [{ label: 'Yes', yesPrice: market.yesPrice, noPrice: market.noPrice }];

  return (
    <motion.div
      initial={{ opacity: 0, x: side === 'left' ? -30 : 30 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: side === 'left' ? -30 : 30 }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
      className="hud-corners flex-1 min-w-0 bg-card border border-card-border overflow-hidden"
      style={{ borderTop: `2px solid ${platformAccent(market.platform)}` }}
    >
      <div className="flex items-center justify-between gap-2 border-b border-card-border p-3">
        <PlatformTag platform={market.platform} />
        <Button
          size="icon"
          variant="ghost"
          className="h-7 w-7"
          onClick={onClose}
          data-testid={`button-close-${side}-panel`}
        >
          <X className="w-4 h-4" />
        </Button>
      </div>

      <div className="p-3 border-b border-card-border">
        <p className="text-sm font-medium line-clamp-2">{market.title}</p>
        {market.outcomeCount > 2 && (
          <Badge variant="outline" className="mt-2 font-mono text-[10px] uppercase tracking-widest">
            {market.outcomeCount} outcomes
          </Badge>
        )}
      </div>

      <ScrollArea className="h-48">
        <div className="p-3 space-y-3">
          {sortedOutcomes.map((outcome, idx) => (
            <OutcomeBar
              key={idx}
              label={outcome.label}
              percentage={outcome.yesPrice}
              isHighlighted={idx === 0}
            />
          ))}
        </div>
      </ScrollArea>

      {market.marketUrl && (
        <div className="p-3 border-t border-card-border bg-muted/30">
          <Button
            size="sm"
            variant="outline"
            className="w-full font-mono text-xs uppercase tracking-widest"
            onClick={() => window.open(market.marketUrl, '_blank')}
            data-testid={`button-open-${side}-market`}
          >
            <ExternalLink className="w-4 h-4 mr-2" />
            Open on {market.platform}
          </Button>
        </div>
      )}
    </motion.div>
  );
}

function EmptySlot({ side }: { side: 'left' | 'right' }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="flex-1 min-h-[200px] border border-dashed border-border flex items-center justify-center text-muted-foreground"
    >
      <div className="text-center p-4">
        <TrendingUp className="w-8 h-8 mx-auto mb-2 opacity-40" strokeWidth={1.5} />
        <p className="font-mono text-xs uppercase tracking-widest">Awaiting {side} feed</p>
        <p className="mt-1 text-xs">Click "Compare {side}" on any market</p>
      </div>
    </motion.div>
  );
}

export function OutcomeComparisonDock() {
  const { leftMarket, rightMarket, isComparing, unpinLeft, unpinRight, clearComparison } = useComparison();

  if (!isComparing) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 100 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 100 }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="fixed bottom-4 left-4 right-4 z-50"
        data-testid="comparison-dock"
      >
        <div className="max-w-4xl mx-auto">
          <div className="tracing-beam bg-background border border-border p-4">
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="section-rule flex-1">
                <ArrowLeftRight className="w-4 h-4 text-primary" />
                <span className="font-mono text-xs font-bold uppercase tracking-[0.25em]">Compare markets</span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={clearComparison}
                className="font-mono text-xs uppercase tracking-widest"
                data-testid="button-clear-comparison"
              >
                <X className="w-4 h-4 mr-1" />
                Close
              </Button>
            </div>

            <div className="flex gap-4">
              <AnimatePresence mode="popLayout">
                {leftMarket ? (
                  <MarketPanel
                    key={`left-${leftMarket.id}`}
                    market={leftMarket}
                    side="left"
                    onClose={unpinLeft}
                  />
                ) : (
                  <EmptySlot side="left" />
                )}
              </AnimatePresence>

              <div className="flex items-center">
                <div className="w-px h-full bg-border" />
              </div>

              <AnimatePresence mode="popLayout">
                {rightMarket ? (
                  <MarketPanel
                    key={`right-${rightMarket.id}`}
                    market={rightMarket}
                    side="right"
                    onClose={unpinRight}
                  />
                ) : (
                  <EmptySlot side="right" />
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
