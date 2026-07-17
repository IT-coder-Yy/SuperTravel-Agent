export type PlanningStatus = 'idle' | 'planning' | 'completed' | 'cancelled' | 'error';

export const statusAfterCompletion = (finishReason?: string): PlanningStatus => {
  if (finishReason === 'cancelled') return 'cancelled';
  if (finishReason === 'failed' || finishReason === 'error') return 'error';
  if (finishReason === 'clarification_required') return 'idle';
  return 'completed';
};

export const statusAfterStreamClosed = (hasStructuredDraft: boolean): PlanningStatus => (
  hasStructuredDraft ? 'completed' : 'error'
);
