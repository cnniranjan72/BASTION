import { useToastStore } from "../store/toast";

const ICON: Record<string, string> = {
  success: "✓",
  error: "!",
  info: "i",
};

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-host" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.kind}`} onClick={() => dismiss(t.id)}>
          <span className="toast__icon">{ICON[t.kind]}</span>
          <span className="toast__message">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
