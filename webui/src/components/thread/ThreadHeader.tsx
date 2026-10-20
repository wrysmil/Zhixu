import { Menu, Moon, Sun, Users } from "lucide-react";
import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { SessionHandleLabel } from "@/components/SessionHandleLabel";
import { cn } from "@/lib/utils";
import type { SessionHandle, TeamBinding } from "@/lib/types";

interface ThreadHeaderProps {
  title: string;
  handle?: SessionHandle | null;
  onToggleSidebar: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  hideSidebarToggleForHostChrome?: boolean;
  hideSidebarToggle?: boolean;
  hideThemeButton?: boolean;
  hideTitle?: boolean;
  actions?: ReactNode;
  workbenchAction?: ReactNode;
  /** 头部右端最后一位控件（例如首页右坞开关），排在所有其它动作之后。 */
  trailingAction?: ReactNode;
  minimal?: boolean;
  promptNavigatorAction?: ReactNode;
  sessionInfoAction?: ReactNode;
  /** Server-confirmed team binding, shown read-only next to the title. */
  teamInfo?: TeamBinding | null;
}

export function ThreadHeader({
  title,
  handle = null,
  onToggleSidebar,
  theme,
  onToggleTheme,
  hideSidebarToggleForHostChrome = false,
  hideSidebarToggle = false,
  hideThemeButton = false,
  hideTitle = false,
  actions,
  workbenchAction,
  trailingAction,
  minimal = false,
  promptNavigatorAction,
  sessionInfoAction,
  teamInfo = null,
}: ThreadHeaderProps) {
  const { t, i18n } = useTranslation();

  return (
    <div
      data-testid="thread-header"
      className={cn(
        "relative z-30 flex items-center justify-between gap-3 px-3 py-1",
        minimal && "h-11",
      )}
    >
      <div className="relative flex min-w-0 items-center gap-2">
        {!hideSidebarToggle ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("thread.header.toggleSidebar")}
            onClick={onToggleSidebar}
            className={cn(
              "h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground",
              hideSidebarToggleForHostChrome && "lg:hidden",
            )}
          >
            <Menu className="h-3.5 w-3.5" />
          </Button>
        ) : null}
        {!minimal && !hideTitle ? (
          <div className="flex min-w-0 items-center rounded-md px-1.5 py-1 text-[12px] font-medium text-muted-foreground">
            <span className="max-w-[min(60vw,32rem)] truncate">{title}</span>
          </div>
        ) : null}
        {minimal && !hideTitle ? (
          <div
            data-testid="thread-header-breadcrumb"
            className="flex min-w-0 items-center gap-2 px-1.5 py-1 text-[12px] text-muted-foreground"
          >
            <span className="shrink-0">
              {i18n.resolvedLanguage?.startsWith("zh") ? "个人空间" : "Personal space"}
            </span>
            <span aria-hidden className="text-border">/</span>
            <strong className="max-w-[min(60vw,32rem)] truncate font-normal text-foreground/75">
              {t("sidebar.newChat")}
            </strong>
          </div>
        ) : null}
        {handle ? (
          <span
            className="flex shrink-0 items-center rounded-md px-1.5 py-1 text-[12px] font-medium"
          >
            <SessionHandleLabel id={handle.id}>
              @{handle.name}
            </SessionHandleLabel>
          </span>
        ) : null}
        {teamInfo ? (
          <span
            data-testid="thread-header-team"
            title={t("thread.composer.team.bound", {
              name: teamInfo.teamName,
              revision: teamInfo.teamRevision,
            })}
            className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[12px] font-medium text-primary/90"
          >
            <Users className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="max-w-[12rem] truncate">
              {t("thread.composer.team.chip", { name: teamInfo.teamName })}
            </span>
          </span>
        ) : null}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        {sessionInfoAction}
        {promptNavigatorAction}
        {actions}
        {!hideThemeButton ? (
          <ThemeButton
            theme={theme}
            onToggleTheme={onToggleTheme}
            label={t("thread.header.toggleTheme")}
          />
        ) : null}
        {workbenchAction}
        {trailingAction}
      </div>

      {!minimal ? (
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-full h-4" />
      ) : null}
    </div>
  );
}

function ThemeButton({
  theme,
  onToggleTheme,
  label,
  className,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  label: string;
  className?: string;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      onClick={onToggleTheme}
      className={cn(
        "host-no-drag h-8 w-8 rounded-full text-muted-foreground/85 hover:bg-accent/40 hover:text-foreground",
        className,
      )}
    >
      {theme === "dark" ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </Button>
  );
}
