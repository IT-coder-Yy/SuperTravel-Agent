import { describe, expect, it } from 'vitest';
import {
  planningStatusLabels,
  type PlanningEventEnvelope,
  type PlanningLifecycleStatus,
  type ProviderQueueWaitingPayload,
} from './travelPlannerTypes';

describe('travelPlannerTypes V3 contract', () => {
  it('maps every lifecycle status to stable Chinese copy', () => {
    const statuses: PlanningLifecycleStatus[] = [
      'idle',
      'planning',
      'completed',
      'completed_degraded',
      'cancelled',
      'error',
      'draft',
      'demo_preview',
      'demo_replaying',
      'demo_completed',
    ];

    expect(Object.keys(planningStatusLabels)).toEqual(statuses);
    expect(planningStatusLabels.completed_degraded).toBe('已完成，部分信息待确认');
    expect(planningStatusLabels.demo_replaying).toBe('示例回放中');
  });

  it('defines the provider queue waiting event shown during realtime verification', () => {
    const event: PlanningEventEnvelope<ProviderQueueWaitingPayload> = {
      event_version: 1,
      event_id: 'evt_queue_1',
      run_id: 'run_1',
      request_id: 'req_1',
      sequence: 2,
      occurred_at: '2026-07-18T12:00:00+08:00',
      type: 'provider_queue_waiting',
      payload: {
        stage: 'realtime_verification',
        provider: 'baidu-map-mcp',
        operation: 'map_search_places',
        priority: 'formal',
        queue_position: 1,
        message: '地点核验排队中',
      },
    };

    expect(event.payload.message).toBe('地点核验排队中');
    expect(event.payload.queue_position).toBeGreaterThanOrEqual(1);
  });
});
