import { createContext, useContext } from 'react';

export type ConfirmTone = 'default' | 'warning' | 'danger';

export interface ConfirmOptions {
  title: string;
  description: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  showCancel?: boolean;
}

export type ConfirmAction = (options: ConfirmOptions) => Promise<boolean>;

export const ConfirmContext = createContext<ConfirmAction | null>(null);

export function useConfirm(): ConfirmAction {
  const confirm = useContext(ConfirmContext);
  if (!confirm) {
    throw new Error('useConfirm must be used inside FeedbackProvider');
  }
  return confirm;
}
