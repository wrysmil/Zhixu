import { useMemo, useRef, useState } from "react";
import {
  Activity,
  Check,
  ChevronDown,
  ChevronLeft,
  ClipboardCheck,
  Globe2,
  ImageIcon,
  LogOut,
  MessageCircle,
  Mic,
  Palette,
  Search,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SIDEBAR_SELECTION_ITEM_CLASS,
  SidebarSelectionHighlight,
} from "@/components/SidebarSelectionHighlight";
import type { SettingsSectionKey } from "@/components/settings/contracts";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const SETTINGS_NAV_ITEMS: Array<{ key: SettingsSectionKey; icon: LucideIcon; fallback: string }> = [
  { key: "overview", icon: Activity, fallback: "Overview" },
  { key: "appearance", icon: Palette, fallback: "Appearance" },
  { key: "models", icon: SlidersHorizontal, fallback: "Models" },
  { key: "image", icon: ImageIcon, fallback: "Image" },
  { key: "voice", icon: Mic, fallback: "Voice" },
  { key: "browser", icon: Globe2, fallback: "Web" },
  { key: "channels", icon: MessageCircle, fallback: "Channels" },
  { key: "eval", icon: ClipboardCheck, fallback: "Evaluation" },
  { key: "runtime", icon: Server, fallback: "System" },
  { key: "advanced", icon: ShieldCheck, fallback: "Security" },
];

const SETTINGS_NAV_GROUPS: Array<{ id: string; zh: string; en: string; keys: SettingsSectionKey[] }> = [
  { id: "personal", zh: "个人", en: "Personal", keys: ["overview", "appearance", "models", "image", "voice"] },
  { id: "integrations", zh: "集成", en: "Integrations", keys: ["browser", "channels"] },
  { id: "system", zh: "系统", en: "System", keys: ["eval", "runtime", "advanced"] },
];

export function standaloneSectionTitle(section: SettingsSectionKey): string {
  if (section === "apps") return "Apps";
  if (section === "automations") return "Automations";
  if (section === "skills") return "Skills";
  if (section === "agents") return "Agents";
  if (section === "teams") return "Teams";
  if (section === "eval") return "Evaluation";
  return SETTINGS_NAV_ITEMS.find((item) => item.key === section)?.fallback ?? "Settings";
}

export function SettingsSidebar({
  activeSection,
  onSelectSection,
  onBackToChat,
  onLogout,
  hostChromeInset,
}: {
  activeSection: SettingsSectionKey;
  onSelectSection: (section: SettingsSectionKey) => void;
  onBackToChat: () => void;
  onLogout?: () => void;
  hostChromeInset?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const zh = (i18n.resolvedLanguage ?? i18n.language ?? "").startsWith("zh");
  const [query, setQuery] = useState("");
  const activeNavItemRef = useRef<HTMLButtonElement>(null);
  const activeItem = SETTINGS_NAV_ITEMS.find((item) => item.key === activeSection)
    ?? SETTINGS_NAV_ITEMS[0];
  const ActiveIcon = activeItem.icon;
  const activeLabel = t(`settings.nav.${activeItem.key}`, {
    defaultValue: activeItem.fallback,
  });
  const navGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return SETTINGS_NAV_GROUPS.map((group) => ({
      ...group,
      items: group.keys
        .map((key) => SETTINGS_NAV_ITEMS.find((item) => item.key === key))
        .filter((item): item is NonNullable<typeof item> => Boolean(item))
        .filter((item) => (
          !needle
          || t(`settings.nav.${item.key}`, { defaultValue: item.fallback }).toLowerCase().includes(needle)
        )),
    })).filter((group) => group.items.length > 0);
  }, [query, t]);

  return (
    <aside
      className={cn(
        "flex w-full shrink-0 flex-col bg-settings-surface px-3 pb-2 pt-4 lg:w-[17rem] lg:border-r lg:border-border/70 lg:px-3 lg:pb-4 lg:pt-4",
      )}
    >
      <button
        type="button"
        onClick={onBackToChat}
        className={cn(
          "touch-target mb-2 inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground lg:mb-3",
          hostChromeInset && "-ml-1",
        )}
      >
        <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
        {t("settings.backToChat")}
      </button>
      <div className="mb-3 px-1 lg:mb-4 lg:px-2">
        <h1 className="text-[18px] font-normal tracking-normal text-foreground">
          {t("settings.sidebar.title")}
        </h1>
      </div>

      <div className="relative mb-4 hidden px-1 lg:block">
        <Search
          className="pointer-events-none absolute left-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/70"
          aria-hidden
        />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={zh ? "搜索设置" : "Search settings"}
          aria-label={zh ? "搜索设置" : "Search settings"}
          className="h-9 w-full rounded-control border border-border/60 bg-muted/30 pl-9 pr-3 text-[12.5px] text-foreground outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-ring/50 focus:bg-background"
        />
      </div>

      <nav
        aria-label={t("settings.sidebar.ariaLabel")}
        className="w-full"
      >
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={`${t("settings.sidebar.title")}: ${activeLabel}`}
              className="touch-target flex h-11 w-full items-center gap-2.5 rounded-control bg-sidebar-accent px-3 text-left text-[13px] font-medium text-foreground transition-colors hover:bg-sidebar-accent/80 lg:hidden"
            >
              <ActiveIcon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{activeLabel}</span>
              <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            sideOffset={6}
            className="w-[var(--radix-dropdown-menu-trigger-width)] max-w-[calc(100vw-1.5rem)]"
          >
            {SETTINGS_NAV_ITEMS.map(({ key, icon: Icon, fallback }) => {
              const active = key === activeSection;
              return (
                <DropdownMenuItem
                  key={key}
                  aria-current={active ? "page" : undefined}
                  onSelect={() => onSelectSection(key)}
                  className={cn(
                    "flex h-10 cursor-default items-center gap-2.5 px-2.5 text-[13px] font-medium",
                    active && "bg-sidebar-accent text-foreground focus:bg-sidebar-accent",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
                  <span className="min-w-0 flex-1 truncate">
                    {t(`settings.nav.${key}`, { defaultValue: fallback })}
                  </span>
                  {active ? <Check className="h-4 w-4 shrink-0" aria-hidden /> : null}
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>

        <SidebarSelectionHighlight
          targetRef={activeNavItemRef}
          activeId={activeSection}
          scope="settings"
          className="relative hidden lg:block"
        >
          {navGroups.map((group) => (
            <div key={group.id} className="mb-3 last:mb-0">
              <p className="px-2.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground/70">
                {zh ? group.zh : group.en}
              </p>
              <div className="space-y-0.5">
                {group.items.map(({ key, icon: Icon, fallback }) => {
                  const active = key === activeSection;
                  return (
                    <button
                      ref={active ? activeNavItemRef : undefined}
                      key={key}
                      type="button"
                      aria-current={active ? "page" : undefined}
                      onClick={() => onSelectSection(key)}
                      className={cn(
                        "touch-target flex h-9 w-full items-center gap-2 rounded-xl px-2.5 text-left text-[13px] font-medium",
                        SIDEBAR_SELECTION_ITEM_CLASS,
                        active
                          ? "text-sidebar-accent-foreground"
                          : "text-muted-foreground/78 hover:bg-muted/45 hover:text-foreground",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
                      <span className="truncate">
                        {t(`settings.nav.${key}`, { defaultValue: fallback })}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </SidebarSelectionHighlight>
      </nav>

      <div className="hidden lg:mt-auto lg:block lg:pt-4">
        {onLogout && !hostChromeInset ? (
          <Button
            type="button"
            variant="ghost"
            onClick={onLogout}
            className="h-9 w-full justify-start gap-2 rounded-control px-2.5 text-[13px] font-medium text-muted-foreground hover:bg-destructive/8 hover:text-destructive"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            {t("app.account.logout")}
          </Button>
        ) : null}
      </div>
    </aside>
  );
}
