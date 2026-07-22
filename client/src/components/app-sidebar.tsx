import { Link, useLocation } from "wouter";
import {
  Calculator,
  Radar,
  History,
  CloudRain,
  FishSymbol,
  LineChart,
  Settings,
  Brain,
  Workflow
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";

const mainNavItems = [
  { path: "/sentinel", label: "Arbitrage Radar", icon: Radar, index: "01" },
  { path: "/weather", label: "Weather Terminal", icon: CloudRain, index: "02" },
  { path: "/whales", label: "Whale Tracker", icon: FishSymbol, index: "03" },
  { path: "/analysis", label: "ML Analysis", icon: Brain, index: "04" },
  { path: "/pipeline", label: "Notebook Pipeline", icon: Workflow, index: "05" },
];

const secondaryNavItems = [
  { path: "/", label: "Manual Calculator", icon: Calculator, index: "06" },
  { path: "/history", label: "Trade History", icon: History, index: "07" },
];

function NavItems({ items }: { items: typeof mainNavItems }) {
  const [location] = useLocation();

  return (
    <SidebarMenu>
      {items.map((item) => {
        const isActive = location === item.path;
        return (
          <SidebarMenuItem key={item.path}>
            <SidebarMenuButton asChild isActive={isActive} tooltip={item.label} className="group/nav relative">
              <Link href={item.path}>
                {/* active rail */}
                <span
                  className={`absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 bg-primary transition-all duration-200 ${
                    isActive ? "opacity-100" : "opacity-0 group-hover/nav:opacity-40"
                  }`}
                />
                <item.icon strokeWidth={1.5} />
                <span className="flex-1 font-medium">{item.label}</span>
                <span
                  className={`font-mono text-[9px] tabular-nums tracking-widest transition-colors ${
                    isActive ? "text-primary" : "text-muted-foreground/50"
                  }`}
                >
                  {item.index}
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

export function AppSidebar() {
  return (
    <Sidebar variant="inset" collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <Link href="/sentinel">
                <div className="hud-corners flex aspect-square size-8 items-center justify-center bg-primary text-primary-foreground">
                  <LineChart className="size-4" />
                </div>
                <div className="flex flex-col gap-0.5 leading-none">
                  <span className="font-black text-base tracking-tight">ARB TERMINAL</span>
                  <span className="text-[10px] text-muted-foreground uppercase tracking-[0.2em] font-mono">Prediction Market Arbitrage</span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="font-mono text-[10px] uppercase tracking-[0.25em]">Terminal Tools</SidebarGroupLabel>
          <SidebarGroupContent>
            <NavItems items={mainNavItems} />
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel className="font-mono text-[10px] uppercase tracking-[0.25em]">Management</SidebarGroupLabel>
          <SidebarGroupContent>
            <NavItems items={secondaryNavItems} />
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <div className="section-rule px-2 group-data-[collapsible=icon]:hidden">
          <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted-foreground/60">sys.nominal</span>
        </div>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton tooltip="Settings">
              <Settings strokeWidth={1.5} />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
