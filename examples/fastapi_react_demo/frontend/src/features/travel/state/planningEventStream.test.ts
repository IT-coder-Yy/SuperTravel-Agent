import { describe, expect, it } from 'vitest';
import { PlanningEventDeduper } from './planningEventStream';

describe('PlanningEventDeduper', () => {
  it('deduplicates replayed events by sequence and event id', () => {
    const deduper = new PlanningEventDeduper();

    expect(deduper.accept({ event_id: 'evt_1', sequence: 1 })).toBe(true);
    expect(deduper.accept({ event_id: 'evt_1', sequence: 2 })).toBe(false);
    expect(deduper.accept({ event_id: 'evt_duplicate_sequence', sequence: 1 })).toBe(false);
    expect(deduper.accept({ event_id: 'evt_3', sequence: 3 })).toBe(true);
    expect(deduper.cursor()).toBe(3);
  });
});
