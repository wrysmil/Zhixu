import i18n from "@/i18n";

import { notify } from "@/lib/toast-events";

/**
 * Settings-style save confirmation.
 *
 * When a save leaves a restart pending, the section's `RestartSettingsFooter`
 * already renders `settings.status.savedRestartApply` inline. Publishing a toast
 * for the same fact would report it twice, so those saves stay inline-only.
 */
export function notifySaved(options: { restartRequired?: boolean } = {}): void {
  if (options.restartRequired) return;
  notify({
    message: i18n.t("toast.saved", { defaultValue: "Saved" }),
    tone: "success",
  });
}

export function notifySaveFailed(message: string): void {
  notify({
    message: i18n.t("toast.saveFailed", {
      defaultValue: "Save failed: {{message}}",
      message,
    }),
    tone: "error",
  });
}
