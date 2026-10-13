import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export interface WorkbenchTabBarItem {
  tabKey: string;
  rowKey: string;
  title: string;
}

interface WorkbenchTabBarProps {
  tabs: readonly WorkbenchTabBarItem[];
  activeTabKey: string | null;
  className?: string;
  onSelectTab: (rowKey: string) => void;
  onNewChat: () => void;
}

export function WorkbenchTabBar({
  tabs,
  activeTabKey,
  className,
  onSelectTab,
  onNewChat,
}: WorkbenchTabBarProps) {
  const { t } = useTranslation();
  if (tabs.length === 0) return null;
  return (
    <div
      data-testid="workbench-tab-bar"
      className={cn(
        "flex h-9 shrink-0 items-center gap-1 border-b border-border/40 bg-sidebar px-2 sm:px-3",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
        {tabs.map((tab) => {
          const active = tab.tabKey === activeTabKey;
          return (
            <button
              key={tab.tabKey}
              type="button"
              title={tab.title}
              aria-current={active ? "page" : undefined}
              onClick={() => onSelectTab(tab.rowKey)}
              className={cn(
                "flex h-7 max-w-[14rem] min-w-0 shrink-0 items-center rounded-control px-2.5 text-[12.5px] leading-none transition-colors",
                active
                  ? "bg-background font-medium text-foreground ring-1 ring-border/60"
                  : "text-sidebar-foreground/80 hover:bg-foreground/5 hover:text-foreground",
              )}
            >
              <span className="truncate">{tab.title}</span>
            </button>
          );
        })}
      </div>
      <button
        type="button"
        aria-label={t("workbench.tabBar.newChat", { defaultValue: "New chat" })}
        title={t("workbench.tabBar.newChat", { defaultValue: "New chat" })}
        onClick={onNewChat}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
      >
        <Plus className="h-4 w-4" />
      </button>
    </div>
  );
}
