export const TOAST_EVENT = "zhixu:toast";

export type ToastTone = "success" | "error";

export interface ToastPayload {
  id: string;
  message: string;
  tone: ToastTone;
  durationMs: number;
}

export interface ToastInput {
  message: string;
  tone?: ToastTone;
  durationMs?: number;
}

const DEFAULT_DURATION_MS: Record<ToastTone, number> = {
  success: 2600,
  error: 4200,
};

export function isToastPayload(value: unknown): value is ToastPayload {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ToastPayload>;
  return (
    typeof candidate.id === "string"
    && typeof candidate.message === "string"
    && (candidate.tone === "success" || candidate.tone === "error")
    && typeof candidate.durationMs === "number"
  );
}

/** Mirrors the `crypto.randomUUID` shim in `main.tsx`; plain-HTTP LAN access leaves it undefined. */
function toastId(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function notify(input: ToastInput): void {
  if (typeof window === "undefined") return;
  const tone = input.tone ?? "success";
  const payload: ToastPayload = {
    id: toastId(),
    message: input.message,
    tone,
    durationMs: input.durationMs ?? DEFAULT_DURATION_MS[tone],
  };
  window.dispatchEvent(new CustomEvent<ToastPayload>(TOAST_EVENT, { detail: payload }));
}
