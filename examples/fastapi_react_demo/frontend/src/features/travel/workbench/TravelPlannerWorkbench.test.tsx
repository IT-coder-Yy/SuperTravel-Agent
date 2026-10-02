import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DayRouteGeometry } from '../../../components/MapComponent';
import type { TripWorkspaceState } from '../../../components/TripWorkspace';
import type { TravelPlannerLocation } from '../state/travelPlannerReducer';
import TravelPlannerWorkbench, { buildCandidateMapLocations } from './TravelPlannerWorkbench';

vi.mock('../../../components/MapComponent', () => ({
  default: ({ locations, dayRoutes, activeGroupId }: {
    locations: TravelPlannerLocation[];
    dayRoutes: DayRouteGeometry[];
    activeGroupId?: string;
  }) => (
    <div
      data-testid="workbench-map"
      data-location-ids={locations.map((location) => location.id).join(',')}
      data-route-days={dayRoutes.map((route) => route.day).join(',')}
      data-active-group={activeGroupId || ''}
    />
  ),
}));

vi.mock('../../../components/TripWorkspace', () => ({
  default: () => <div data-testid="trip-workspace" />,
}));

const locations: TravelPlannerLocation[] = [
  { id: 'west-lake', poi_id: 'poi-west-lake', name: '西湖', lat: 30.25, lng: 120.15, day: 1, order: 1 },
  { id: 'lingyin', poi_id: 'poi-lingyin', name: '灵隐寺', lat: 30.24, lng: 120.1, day: 1, order: 2 },
  { id: 'canal', poi_id: 'poi-canal', name: '京杭大运河', lat: 30.32, lng: 120.14, day: 2, order: 1 },
];

const locationGroups = [
  { groupId: 'day-1', title: '第1天路线', locations: locations.slice(0, 2) },
  { groupId: 'day-2', title: '第2天路线', locations: locations.slice(2) },
];

const dayRoutes: DayRouteGeometry[] = [
  { day: 1, plan_version: 1, status: 'ready', coordinate_system: 'WGS84', legs: [] },
  { day: 2, plan_version: 1, status: 'ready', coordinate_system: 'WGS84', legs: [] },
];

const workspace: TripWorkspaceState = {
  days: [], locations: [], sources: [], budget: null, validation: null, repair: null,
};

const renderWorkbench = (overrides: Partial<React.ComponentProps<typeof TravelPlannerWorkbench>> = {}) => {
  const props: React.ComponentProps<typeof TravelPlannerWorkbench> = {
    sidePanelRef: React.createRef<HTMLDivElement>(),
    hasTripWorkspaceData: true,
    sidePanelMapPercent: 50,
    effectiveMapLocations: locations,
    candidateMapLocations: [{
      id: 'candidate-tea', poi_id: 'poi-tea', name: '龙井村', lat: 30.211, lng: 120.111, day: '候选', order: 1,
    }],
    dayRoutes,
    activeMapGroupId: 'day-1',
    selectedLocationId: '',
    mapLocationGroups: locationGroups,
    workspaceLocationGroups: locationGroups,
    tripWorkspace: workspace,
    activeTripDocument: null,
    hasActiveTripPlan: true,
    canUndoEdit: false,
    editLoading: false,
    onStartResize: vi.fn(),
    onSelectGroup: vi.fn(),
    onSelectLocation: vi.fn(),
    onUndoEdit: vi.fn(),
    onFocusMap: vi.fn(),
    onFocusWorkspace: vi.fn(),
    onQuickAction: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<TravelPlannerWorkbench {...props} />) };
};

describe('TravelPlannerWorkbench', () => {
  it('only derives candidates that have a stable activity, POI, and coordinates', () => {
    expect(buildCandidateMapLocations({
      candidate_pool: [
        {
          activity_id: 'candidate-tea', title: '龙井品茶',
          place: {
            poi_id: 'poi-tea', name: '龙井村', category: 'food', summary: '未排期茶园体验',
            coordinates: { latitude: 30.211, longitude: 120.111 },
          },
        },
        {
          activity_id: 'candidate-without-coordinates',
          place: { poi_id: 'poi-missing', name: '坐标待确认地点' },
        },
      ],
    })).toEqual([{
      id: 'candidate-tea', poi_id: 'poi-tea', name: '龙井村', lat: 30.211, lng: 120.111,
      description: '未排期茶园体验', category: 'food', day: '候选', order: 1,
    }]);
  });

  it('keeps overview complete, filters a single day with only that day route, and leaves candidates unrouted', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const { props, rerender } = renderWorkbench({ onSelectGroup, onSelectLocation });

    expect(screen.getByTestId('workbench-map').dataset.locationIds).toBe('west-lake,lingyin,canal');
    expect(screen.getByTestId('workbench-map').dataset.routeDays).toBe('1,2');

    fireEvent.click(screen.getByText('单日'));
    expect(onSelectGroup).toHaveBeenCalledWith('day-1');
    expect(screen.getByTestId('workbench-map').dataset.locationIds).toBe('west-lake,lingyin');
    expect(screen.getByTestId('workbench-map').dataset.routeDays).toBe('1');

    fireEvent.click(screen.getByText('Day 2'));
    expect(onSelectGroup).toHaveBeenCalledWith('day-2');

    rerender(<TravelPlannerWorkbench {...props} activeMapGroupId="day-2" />);
    expect(screen.getByTestId('workbench-map').dataset.locationIds).toBe('canal');
    expect(screen.getByTestId('workbench-map').dataset.routeDays).toBe('2');

    fireEvent.click(screen.getByText('候选'));
    expect(screen.getByTestId('workbench-map').dataset.locationIds).toBe('candidate-tea');
    expect(screen.getByTestId('workbench-map').dataset.routeDays).toBe('');
    expect(onSelectLocation).toHaveBeenCalledWith('');
  });

  it('opens the full editor from C1 and keeps it mounted across close and reopen', () => {
    renderWorkbench();
    expect(screen.queryByTestId('trip-workspace')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /编辑行程/ }));
    const editor = screen.getByTestId('trip-workspace');
    fireEvent.click(screen.getByRole('button', { name: /关闭|Close/ }));
    fireEvent.click(screen.getByRole('button', { name: /编辑行程/ }));
    expect(screen.getByTestId('trip-workspace')).toBe(editor);
  });

  it('selects a mapped stop by its POI id and keeps unlocated activities visible', () => {
    const { props } = renderWorkbench({
      effectiveMapLocations: [{ ...locations[0], id: 'poi-west-lake', poi_id: undefined }],
      tripWorkspace: { ...workspace, days: [{
        id: 'day-1', day: 1, date: '2026-10-03', theme: '西湖漫步', revision: 1,
        estimated_cost: null, warnings: [], data_type: null, source: null, sources: [],
        activities: ['西湖', '待确认茶馆'].map((title, index) => ({
          id: `activity-${index}`, day: 1, start_time: '09:30', end_time: null, title,
          place: { name: title, poi_id: index === 0 ? 'poi-west-lake' : 'unknown' },
          estimated_cost: null, notes: [], data_type: null, source: null, sources: [], evidence_refs: [], route_to_next: null,
        })),
      }] },
    });
    fireEvent.click(screen.getByRole('button', { name: /1 西湖/ }));
    expect(props.onSelectLocation).toHaveBeenCalledWith('poi-west-lake');
    expect(screen.getByRole('button', { name: /待确认茶馆.*位置待确认/ })).toBeTruthy();
  });
});
