import { describe, expect, it } from 'vitest';
import {
  buildDeleteLocationPrompt,
  buildMoveLocationPrompt,
  buildOpenStreetMapUrl,
  buildReplaceLocationPrompt,
  quickActionPrompts,
} from './TripWorkspaceActions';

const location = { name: '故宫博物院', day: 1 };

describe('TripWorkspaceActions', () => {
  it('builds executable location update prompts with day context', () => {
    expect(buildReplaceLocationPrompt(location)).toContain('第1天的地点“故宫博物院”');
    expect(buildDeleteLocationPrompt(location)).toContain('删除第1天的地点“故宫博物院”');
    expect(buildMoveLocationPrompt(location, '第2天')).toContain('移到第2天');

    for (const prompt of [
      buildReplaceLocationPrompt(location),
      buildDeleteLocationPrompt(location),
      buildMoveLocationPrompt(location),
    ]) {
      expect(prompt).toContain('直接更新当前旅行规划');
      expect(prompt).toContain('完整结构化行程');
    }
  });

  it('keeps every quick action tied to a structured replanning request', () => {
    expect(Object.keys(quickActionPrompts)).toEqual([
      'slow_down',
      'lower_budget',
      'family_friendly',
      'hidden_gems',
      'reduce_commute',
    ]);
    Object.values(quickActionPrompts).forEach((prompt) => {
      expect(prompt).toContain('直接更新当前旅行规划');
      expect(prompt).toContain('同步更新日程、地点、预算和校验结果');
    });
  });

  it('builds a precise map URL and rejects invalid coordinates', () => {
    expect(buildOpenStreetMapUrl(39.9163, 116.3972)).toContain('mlat=39.916300&mlon=116.397200');
    expect(buildOpenStreetMapUrl(Number.NaN, 116.3972)).toBeNull();
  });
});
