import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TripProductTools, { buildChecklistItem, buildEffectiveTripPlan, buildTripNote } from './TripProductTools';

describe('TripProductTools', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ templates: [{
        id: 'classic', name: '经典三日游', audience: ['首次到访'], budget_level: '中等', pace: 'balanced',
        default_days: 3, preferences: ['经典景点'], prompt: '使用模板并继续逐项澄清',
      }] }),
    }));
  });

  it('uses a template as intent and continues clarification', async () => {
    const onUseTemplate = vi.fn();
    render(<TripProductTools plan={null} workspace={{ days: [], locations: [], sources: [], budget: null, validation: null, repair: null }} onUseTemplate={onUseTemplate} />);
    fireEvent.click(screen.getByRole('button', { name: /行程工具/ }));
    await waitFor(() => expect(screen.getByText('经典三日游')).not.toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '使用并继续澄清' }));
    expect(onUseTemplate).toHaveBeenCalledWith('使用模板并继续逐项澄清');
  });

  it('binds checklist items and notes to concrete activities', () => {
    const workspace = {
      days: [{
        id: 'day-1', day: 1, date: null, theme: '西湖', revision: 0, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [{
          id: 'activity-west-lake', day: 1, start_time: '09:00', end_time: '11:00',
          title: '游览西湖', place: { name: '西湖' }, estimated_cost: 0, notes: [],
          data_type: null, source: null, sources: [], evidence_refs: [], route_to_next: null,
        }],
      }],
      locations: [{ id: 'poi-west-lake', name: '西湖', lat: 30.24, lng: 120.15 }],
      sources: [], budget: null, validation: null, repair: null,
    };
    const item = buildChecklistItem('item-1', '购买门票', 'activity:activity-west-lake', workspace);
    const note = buildTripNote('note-1', '下午拍照', 'activity', 'activity-west-lake', workspace);
    expect(item).toMatchObject({ activity_id: 'activity-west-lake', day: 1 });
    expect(note).toMatchObject({ target_type: 'activity', target_id: 'activity-west-lake', activity_id: 'activity-west-lake', day: 1 });
  });

  it('uses a restored workspace when activeTripPlan is absent', () => {
    const workspace = {
      days: [{ id: 'day-1', day: 1, date: null, theme: null, revision: 1, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [] }],
      locations: [], sources: [], budget: null, validation: null, repair: null,
    };
    expect(buildEffectiveTripPlan(null, workspace)).toMatchObject({
      plan_id: 'workspace-restored-draft', version: 1, days: 1,
    });
  });

  it('keeps export and share document aligned with the active plan version', async () => {
    const onDocumentChange = vi.fn();
    const workspace = {
      days: [{ id: 'day-1', day: 1, date: null, theme: '新版', revision: 2, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [{
          id: 'new-activity', day: 1, start_time: null, end_time: null, title: '新版景点',
          place: { poi_id: 'new-poi', name: '新版景点', lat: 30.1, lng: 120.1 }, estimated_cost: 0,
          notes: [], data_type: null, source: null, sources: [], evidence_refs: [], route_to_next: null,
        }] }],
      locations: [{ id: 'new-poi', name: '新版景点', lat: 30.1, lng: 120.1 }],
      sources: [], budget: null, validation: null, repair: null,
    };
    const plan = {
      plan_id: 'plan-1', version: 2, title: '杭州新版行程', intent: { destination: '杭州', days: 1 }, days: 1,
      trip_days: workspace.days.map((day) => ({ ...day, activities: day.activities.map((activity) => ({ ...activity, activity_id: activity.id })) })),
    };
    const document = {
      schema_version: '2.0', plan_id: 'plan-1', version: 1, title: '旧版', intent: {},
      itinerary: { status: 'ready', days: [] }, map_guidance: { location_ids: [] }, delivery: {}, checklist: [], notes: [],
    };

    render(<TripProductTools plan={plan} document={document} workspace={workspace} onDocumentChange={onDocumentChange} />);

    await waitFor(() => expect(onDocumentChange).toHaveBeenCalled());
    const emitted = onDocumentChange.mock.calls.at(-1)?.[0];
    expect(emitted).toMatchObject({ version: 2, title: '杭州新版行程' });
    expect(emitted.itinerary.days[0].activities[0].title).toBe('新版景点');
    expect(emitted.map_guidance.location_ids).toEqual(['new-poi']);
    expect(emitted.delivery.markdown_filename).toBe('杭州新版行程-v2.md');
  });
});
