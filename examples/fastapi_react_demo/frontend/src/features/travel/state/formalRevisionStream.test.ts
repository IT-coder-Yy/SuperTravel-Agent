import { describe, expect, it } from 'vitest';
import formalFixture from '../../../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import { PlanningEventDeduper } from './planningEventStream';
import { readFormalRevisionStream } from './formalRevisionStream';
import type { TravelPlanDocumentV3 } from './travelPlannerTypes';

const isFormalDocument = (value: unknown): value is TravelPlanDocumentV3 => {
  if (!value || typeof value !== 'object') return false;
  const document = value as Record<string, unknown>;
  return document.schema_version === '3.0'
    && typeof document.plan_id === 'string'
    && Number.isInteger(Number(document.revision));
};

const eventLine = (event: Record<string, unknown>): string => `data: ${JSON.stringify(event)}\n\n`;

describe('formalRevisionStream', () => {
  it('仅在匹配操作的完整 V3 文档抵达后提交，并保留正式修订生命周期事件', async () => {
    const document = { ...formalFixture, revision: 2, status: 'formal' } as unknown as TravelPlanDocumentV3;
    const planningEvents: string[] = [];
    const response = new Response([
      eventLine({ event_id: 'revision-start', sequence: 1, request_id: 'apply-1', type: 'plan_revision_started' }),
      eventLine({ event_id: 'revision-section', sequence: 2, request_id: 'apply-1', type: 'plan_revision_section' }),
      eventLine({ event_id: 'revision-completed', sequence: 3, request_id: 'apply-1', type: 'plan_revision_completed' }),
      eventLine({ event_id: 'formal-document', sequence: 4, request_id: 'apply-1', type: 'trip_plan', document }),
    ].join(''));

    const resolved = await readFormalRevisionStream(response, {
      operationId: 'apply-1',
      expectedRevision: 2,
      deduper: new PlanningEventDeduper(),
      isFormalDocument,
      onPlanningEvent: (event) => planningEvents.push(String(event.type)),
    });

    expect(resolved).toEqual(document);
    expect(planningEvents).toEqual(['plan_revision_started', 'plan_revision_completed']);
  });

  it('忽略其他操作的事件，并拒绝版本号不匹配的正式文档', async () => {
    const document = { ...formalFixture, revision: 3, status: 'formal' } as unknown as TravelPlanDocumentV3;
    const ignoredResponse = new Response(eventLine({
      event_id: 'other-operation', sequence: 1, request_id: 'other', type: 'trip_plan', document,
    }));
    const options = {
      operationId: 'apply-2',
      expectedRevision: 2,
      deduper: new PlanningEventDeduper(),
      isFormalDocument,
    };

    await expect(readFormalRevisionStream(ignoredResponse, options)).resolves.toBeNull();
    await expect(readFormalRevisionStream(new Response(eventLine({
      event_id: 'wrong-revision', sequence: 1, request_id: 'apply-2', type: 'trip_plan', document,
    })), { ...options, deduper: new PlanningEventDeduper() })).rejects.toThrow('正式版本已变化');
  });
});
