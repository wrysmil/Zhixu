import type { ReactNode } from "react";

export interface ThreadFrameProps {
  header: ReactNode;
  children: ReactNode;
  composer: ReactNode;
  details?: ReactNode;
}

/**
 * Pure chat-page layout skeleton (header / transcript / composer / optional
 * task-details pane). All behavior stays in the caller: ThreadShell keeps the
 * streaming and history hooks, PreviewThread passes static content.
 */
export function ThreadFrame({ header, children, composer, details }: ThreadFrameProps) {
  return (
    <div className="flex h-full min-w-0 flex-1 overflow-hidden">
      <div className="thread-layout flex min-w-0 flex-1 flex-col bg-background">
        <header className="shrink-0 border-b border-border/70">{header}</header>
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
        <div className="thread-composer-dock shrink-0">{composer}</div>
      </div>
      {details ? (
        <aside className="hidden min-w-0 shrink-0 overflow-hidden border-l border-border/70 bg-settings-surface lg:block lg:w-[340px] xl:w-[380px]">
          {details}
        </aside>
      ) : null}
    </div>
  );
}
