import { create } from "zustand";

export interface Toast {
  id: string;
  kind: "success" | "error" | "info";
  message: string;
}

interface ToastState {
  toasts: Toast[];
  push: (kind: Toast["kind"], message: string) => void;
  dismiss: (id: string) => void;
}

const AUTO_DISMISS_MS = 4000;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (kind, message) => {
    const id = crypto.randomUUID();
    set({ toasts: [...get().toasts, { id, kind, message }] });
    setTimeout(() => get().dismiss(id), AUTO_DISMISS_MS);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

// Thin free functions so call sites don't need useToastStore.getState()
// sprinkled everywhere — same ergonomic reasoning as the `api` object in
// api/client.ts.
export const toast = {
  success: (message: string) => useToastStore.getState().push("success", message),
  error: (message: string) => useToastStore.getState().push("error", message),
  info: (message: string) => useToastStore.getState().push("info", message),
};
