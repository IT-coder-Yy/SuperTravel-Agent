import { describe, expect, it } from 'vitest';
import {
  createEmptyTripWorkspace,
  createInitialTravelPlannerState,
  setTravelPlannerField,
  travelPlannerReducer,
} from './travelPlannerReducer';

const initialState = () => createInitialTravelPlannerState({
  showMap: true,
  sidePanelMapPercent: 60,
});

describe('travelPlannerReducer', () => {
  it('owns functional field updates in one state container', () => {
    const state = initialState();
    const updated = travelPlannerReducer(
      state,
      setTravelPlannerField('chatPanelWidthPercent', (current) => current - 10),
    );

    expect(updated.chatPanelWidthPercent).toBeCloseTo(60.6);
    expect(state.chatPanelWidthPercent).toBe(70.6);
  });

  it('restores a formal trip atomically and opens its map', () => {
    const workspace = {
      ...createEmptyTripWorkspace(),
      locations: [{ id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15 }],
    };
    const restored = travelPlannerReducer(initialState(), {
      type: 'trip/restore',
      plan: { plan_id: 'plan-1', version: 1 },
      document: { schema_version: '3.0' },
      workspace,
      locations: workspace.locations,
    });

    expect(restored.planningStatus).toBe('completed');
    expect(restored.showMap).toBe(true);
    expect(restored.activeTripPlan?.plan_id).toBe('plan-1');
    expect(restored.tripWorkspace).toBe(workspace);
  });

  it('resets trip facts without discarding the user layout proportions', () => {
    const state = {
      ...initialState(),
      planningStatus: 'completed' as const,
      chatPanelWidthPercent: 64,
      sidePanelMapPercent: 58,
      activeTripPlan: { plan_id: 'plan-1' },
      tripWorkspace: {
        ...createEmptyTripWorkspace(),
        locations: [{ id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15 }],
      },
    };
    const reset = travelPlannerReducer(state, { type: 'trip/reset' });

    expect(reset.planningStatus).toBe('idle');
    expect(reset.activeTripPlan).toBeNull();
    expect(reset.tripWorkspace.locations).toEqual([]);
    expect(reset.chatPanelWidthPercent).toBe(64);
    expect(reset.sidePanelMapPercent).toBe(58);
  });

  it('clears the previous formal facts only after structured replacement begins', () => {
    const state = {
      ...initialState(),
      planningStatus: 'planning' as const,
      activeTripPlan: { plan_id: 'old-formal' },
      mapSuppressed: true,
    };
    const replaced = travelPlannerReducer(state, { type: 'trip/begin-structured-replacement' });

    expect(replaced.planningStatus).toBe('planning');
    expect(replaced.activeTripPlan).toBeNull();
    expect(replaced.mapSuppressed).toBe(false);
  });

  it('keeps the current trip when an unsupported action reaches the reducer', () => {
    const state = {
      ...initialState(),
      activeTripPlan: { plan_id: 'formal-plan' },
    };

    expect(travelPlannerReducer(state, { type: 'unsupported' } as never)).toBe(state);
  });
});
