import { describe, expect, it } from 'vitest';
import { selectActiveFormalDocument } from './formalDocumentSelection';
import type { TravelPlanDocumentV3 } from './travelPlannerTypes';

const document = (revision: number): TravelPlanDocumentV3 => ({
  schema_version: '3.0',
  plan_id: 'plan-download',
  revision,
} as TravelPlanDocumentV3);

describe('selectActiveFormalDocument', () => {
  it('uses the formal snapshot stored for the active chat', () => {
    expect(selectActiveFormalDocument('chat-1', { 'chat-1': document(2) }, [])?.revision).toBe(2);
  });

  it('falls back to the latest non-superseded formal message when the ref key differs', () => {
    expect(selectActiveFormalDocument('chat-1', {}, [
      { formalDocument: document(1), supersededByRevision: 2 },
      { formalDocument: document(2) },
    ])?.revision).toBe(2);
  });
});
