import { describe, expect, it } from 'vitest';
import { planningStatusLabels, type PlanningLifecycleStatus } from './travelPlannerTypes';

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
});
