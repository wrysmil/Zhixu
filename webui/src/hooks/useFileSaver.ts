import { useCallback, useEffect, useState } from "react";

import { saveFileContent, type WebUIMutationTransport } from "@/lib/api";
import type { FilePreviewPayload } from "@/lib/types";

interface UseFileSaverOptions {
  transport: WebUIMutationTransport | null;
  sessionKey: string;
  scope: string | null;
  payload: FilePreviewPayload | null;
  onSaved: (next: FilePreviewPayload) => void;
}

/** Direct-edit state for a text file preview backed by ``workspace.file.save``. */
export function useFileSaver({
  transport,
  sessionKey,
  scope,
  payload,
  onSaved,
}: UseFileSaverOptions) {
  const [draft, setDraftState] = useState(payload?.content ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const path = payload?.path ?? "";
  const content = payload?.content ?? "";

  useEffect(() => {
    setDraftState(content);
    setSaving(false);
    setError(null);
    setSaved(false);
    // Reset editor state only when the previewed file changes, not on save.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    setDraftState(content);
  }, [content]);

  const editable = Boolean(transport && payload && !payload.truncated);
  const dirty = editable && draft !== content;

  const setDraft = useCallback((value: string) => {
    setDraftState(value);
    setSaved(false);
    setError(null);
  }, []);

  const revert = useCallback(() => {
    setDraftState(content);
    setSaved(false);
    setError(null);
  }, [content]);

  const save = useCallback(async () => {
    if (!transport || !payload || saving || draft === payload.content) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const result = await saveFileContent(
        transport,
        sessionKey,
        payload.path,
        draft,
        scope,
      );
      onSaved({ ...payload, content: draft, size: result.size, truncated: false });
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }, [draft, onSaved, payload, saving, scope, sessionKey, transport]);

  return {
    editable,
    draft,
    setDraft,
    dirty,
    saving,
    error,
    saved,
    save,
    revert,
  };
}
