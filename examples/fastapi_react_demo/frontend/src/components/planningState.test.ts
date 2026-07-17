import { describe, expect, it } from 'vitest';
import { statusAfterCompletion, statusAfterStreamClosed } from './planningState';

describe('planningState', () => {
  it('converges terminal events to explicit states', () => {
    expect(statusAfterCompletion('completed')).toBe('completed');
    expect(statusAfterCompletion('cancelled')).toBe('cancelled');
    expect(statusAfterCompletion('failed')).toBe('error');
    expect(statusAfterCompletion('clarification_required')).toBe('idle');
  });

  it('keeps a generated draft when SSE closes without a terminal event', () => {
    expect(statusAfterStreamClosed(true)).toBe('completed');
    expect(statusAfterStreamClosed(false)).toBe('error');
  });
});
