import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Search, Smile } from "lucide-react";

import { AgentGlyph } from "@/components/ui/agent-glyph";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { EMOJI_GROUP_LABEL_KEY } from "@/lib/agents/i18n";
import {
  LUCIDE_AGENT_ICON_GROUPS,
  lucideAgentIconToken,
} from "@/lib/agents/icons";
import { cn } from "@/lib/utils";

const EMOJI_GROUPS: Array<{ id: string; label: string; items: string[] }> = [
  {
    id: "agents",
    label: "智能体",
    items: ["🦊", "🤖", "🧠", "🧭", "🔮", "👾", "🐙", "🦉", "🐝", "🦋", "🎯", "🧩"],
  },
  {
    id: "roles",
    label: "角色",
    items: ["🕵️", "👩‍💻", "👨‍🍳", "🧑‍🏫", "👩‍⚕️", "👷", "🧑‍🚀", "🧙", "🃏", "⚖️", "💼", "📚"],
  },
  {
    id: "work",
    label: "工作",
    items: ["📊", "📈", "📉", "🗂", "📝", "📮", "🗓", "⏰", "🔍", "🛠", "⚙️", "🧪"],
  },
  {
    id: "nature",
    label: "自然",
    items: ["🌱", "🌳", "🌊", "🔥", "⭐", "🌙", "☁️", "⛰", "🌋", "❄️", "🌈", "🍃"],
  },
  {
    id: "objects",
    label: "物品",
    items: ["💡", "🔑", "🧭", "🛡", "⚡", "🎯", "🧲", "💎", "🕯", "🪄", "🔭", "📡"],
  },
  {
    id: "symbols",
    label: "符号",
    items: ["✨", "✅", "❌", "⚠️", "❓", "💡", "🔁", "⏩", "🔗", "🎵", "💬", "🧿"],
  },
  {
    id: "food",
    label: "食物",
    items: ["🍎", "🍊", "🍋", "🍇", "🍓", "🍒", "🥑", "🍕", "🍜", "🍣", "🍰", "☕"],
  },
  {
    id: "activity",
    label: "活动",
    items: ["🎉", "🎈", "🎁", "🏆", "🎵", "🎬", "🎮", "🎲", "🏃", "🧘", "🎤", "🥇"],
  },
];
const ALL_EMOJI = EMOJI_GROUPS.flatMap((group) => group.items);

type IconTab = "lucide" | "emoji";

/**
 * Agent 图标选择器：默认从内置 Lucide 图标集里挑，保留 Emoji 作为备选。
 * 存入档案的字符串是 ``lucide:<name>`` 或 emoji 本身，图片路径仍可直接编辑。
 */
export function AgentIconPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (icon: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<IconTab>("lucide");

  const needle = query.trim().toLowerCase();
  const lucideGroups = useMemo(() => {
    if (!needle) return LUCIDE_AGENT_ICON_GROUPS;
    return LUCIDE_AGENT_ICON_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter(
        (name) => name.includes(needle) || group.label.includes(needle),
      ),
    })).filter((group) => group.items.length > 0);
  }, [needle]);
  const emojiGroups = useMemo(() => {
    if (!needle) return EMOJI_GROUPS;
    return EMOJI_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter(
        (item) => group.label.includes(needle) || item.includes(needle),
      ),
    })).filter((group) => group.items.length > 0);
  }, [needle]);

  const groups = tab === "lucide" ? lucideGroups : emojiGroups;

  const choose = (icon: string) => {
    onChange(icon);
    setOpen(false);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("settings.agents.icon.ariaLabel", "选择图标")}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control border border-border bg-background transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          {value ? (
            <AgentGlyph icon={value} size={18} />
          ) : (
            <Smile className="h-4 w-4 text-muted-foreground" aria-hidden />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[20rem] p-3">
        <div className="relative mb-2">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.agents.icon.search", "搜索图标")}
            aria-label={t("settings.agents.icon.search", "搜索图标")}
            className="h-8 pl-8 text-[13px]"
          />
        </div>
        <div role="tablist" className="mb-2 flex items-center gap-1 rounded-control bg-muted/50 p-0.5">
          {([
            ["lucide", t("settings.agents.icon.lucideTab", { defaultValue: "图标" })],
            ["emoji", t("settings.agents.icon.emojiTab", { defaultValue: "Emoji" })],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={cn(
                "flex-1 rounded-[calc(var(--radius-control)-2px)] px-2 py-1 text-[12px] font-medium transition-colors",
                tab === id
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        {groups.length === 0 ? (
          <p className="px-1 py-6 text-center text-[12px] text-muted-foreground">
            {t("settings.agents.icon.empty", "没有匹配的图标")}
          </p>
        ) : (
          <div className="max-h-64 space-y-2 overflow-y-auto scrollbar-thin">
            {groups.map((group) => (
              <div key={group.id}>
                <p className="mb-1 px-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {tab === "lucide"
                    ? t(`settings.agents.icon.groups.${group.id}`, group.label)
                    : t(EMOJI_GROUP_LABEL_KEY[group.id], group.label)}
                </p>
                <div className="grid grid-cols-8 gap-0.5">
                  {group.items.map((item) => {
                    const icon = tab === "lucide" ? lucideAgentIconToken(item) : item;
                    const active = icon === value;
                    return (
                      <button
                        key={icon}
                        type="button"
                        aria-pressed={active}
                        aria-label={item}
                        title={item}
                        onClick={() => choose(icon)}
                        className={cn(
                          "flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-muted",
                          active && "bg-muted ring-1 ring-ring",
                        )}
                      >
                        {tab === "lucide" ? (
                          <AgentGlyph icon={icon} size={15} />
                        ) : (
                          <span className="text-[15px] leading-none">{item}</span>
                        )}
                        {active ? <Check className="sr-only" aria-hidden /> : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

export { ALL_EMOJI };
