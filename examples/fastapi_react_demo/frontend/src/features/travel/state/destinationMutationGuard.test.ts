import { describe, expect, it } from 'vitest';
import {
  detectDestinationMutation,
  extractExplicitDestination,
  normalizeDestinationName,
} from './destinationMutationGuard';

describe('destinationMutationGuard', () => {
  it('extracts an explicit planning destination without mistaking the origin for it', () => {
    expect(extractExplicitDestination('从上海出发，规划南京三日游')).toBe('南京');
    expect(extractExplicitDestination('帮我去苏州旅游，安排两天')).toBe('苏州');
  });

  it('normalizes city suffixes before comparing destinations', () => {
    expect(normalizeDestinationName(' 杭州市 ')).toBe('杭州');
    expect(detectDestinationMutation('杭州', '把杭州市的节奏安排慢一些')).toBeNull();
  });

  it('requires a new trip when a clear new destination is entered', () => {
    expect(detectDestinationMutation('杭州', '从上海出发，规划南京3天行程')).toEqual({
      currentDestination: '杭州',
      nextDestination: '南京',
    });
  });

  it('does not block ordinary follow-up requests without an explicit destination', () => {
    expect(detectDestinationMutation('杭州', '把第二天安排得轻松一点')).toBeNull();
  });
});
