import { useState } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { MessageCircle, TrendingUp, TrendingDown } from 'lucide-react';

interface Answer {
  label: string;
  yesPrice: number;
  noPrice: number;
  volume?: number;
}

interface MultipleAnswersPopoverProps {
  answers: Answer[];
  platformName: string;
  marketTitle: string;
}

export function MultipleAnswersPopover({ answers, platformName, marketTitle }: MultipleAnswersPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (answers.length <= 1) {
    return null;
  }

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Badge 
          variant="outline" 
          className="cursor-pointer hover-elevate gap-1 text-xs"
          data-testid="badge-multiple-answers"
        >
          <MessageCircle className="w-3 h-3" />
          {answers.length} Answers
        </Badge>
      </PopoverTrigger>
      <PopoverContent
        className="hud-corners w-80 p-0 relative"
        side="top"
        align="center"
        sideOffset={12}
      >
        <div className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-4 h-4 rotate-45 bg-popover border-r border-b border-popover-border" />

        <div className="p-3 border-b bg-muted/30">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{platformName}</p>
          <p className="text-sm font-medium line-clamp-2">{marketTitle}</p>
        </div>
        
        <ScrollArea className="max-h-64">
          <div className="p-2 space-y-1">
            {answers.map((answer, index) => (
              <div 
                key={index}
                className="p-2 rounded-sm hover:bg-muted/50 transition-colors"
                data-testid={`answer-item-${index}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium truncate flex-1">
                    {answer.label}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1">
                  <div className="flex items-center gap-1 text-xs">
                    <TrendingUp className="w-3 h-3 text-green-500" />
                    <span className="text-muted-foreground">Yes:</span>
                    <span className="font-mono">{(answer.yesPrice * 100).toFixed(0)}¢</span>
                  </div>
                  <div className="flex items-center gap-1 text-xs">
                    <TrendingDown className="w-3 h-3 text-red-500" />
                    <span className="text-muted-foreground">No:</span>
                    <span className="font-mono">{(answer.noPrice * 100).toFixed(0)}¢</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
        
        <div className="p-2 border-t bg-muted/20">
          <p className="font-mono text-[10px] uppercase tracking-widest text-center text-muted-foreground">
            Tap outside to close
          </p>
        </div>
      </PopoverContent>
    </Popover>
  );
}
