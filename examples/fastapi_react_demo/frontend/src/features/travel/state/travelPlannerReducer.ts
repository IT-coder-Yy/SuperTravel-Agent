import type { SetStateAction } from 'react';
import type { DayRouteGeometry } from '../../../components/MapComponent';
import type { PlanningStatus } from '../../../components/planningState';
import type { TripWorkspaceState } from '../../../components/TripWorkspace';
import type { MapAnchorKind } from '../../../components/map/mapMarkerPresentation';

export type MobilePrimaryView = 'chat' | 'trip' | 'map';

export interface TravelPlannerLocation {
  id: string;
  name: string;
  lat: number;
  lng: number;
  description?: string;
  category?: string;
  day?: number | string;
  order?: number | string;
  poi_id?: string;
  anchor_kind?: MapAnchorKind;
  anchor_label?: string;
}

export interface TravelPlannerState {
  planningStatus: PlanningStatus;
  showMap: boolean;
  chatPanelWidthPercent: number;
  mapLocations: TravelPlannerLocation[];
  mapSuppressed: boolean;
  tripWorkspace: TripWorkspaceState;
  activeTripPlan: Record<string, unknown> | null;
  activeTripDocument: Record<string, unknown> | null;
  previousTripPlan: Record<string, unknown> | null;
  dayRoutes: DayRouteGeometry[];
  activeMapGroupId: string;
  selectedLocationId: string;
  sidePanelMapPercent: number;
  mobilePrimaryView: MobilePrimaryView;
}

export interface TravelPlannerInitialOptions {
  showMap: boolean;
  sidePanelMapPercent: number;
}

type SetFieldAction = {
  [Field in keyof TravelPlannerState]: {
    type: 'field/set';
    field: Field;
    value: SetStateAction<TravelPlannerState[Field]>;
  }
}[keyof TravelPlannerState];

export type TravelPlannerAction =
  | SetFieldAction
  | { type: 'trip/reset'; showMap?: boolean }
  | {
      type: 'trip/restore';
      plan: Record<string, unknown> | null;
      document: Record<string, unknown> | null;
      workspace: TripWorkspaceState;
      locations: TravelPlannerLocation[];
    }
  | { type: 'trip/begin-structured-replacement' };

export const createEmptyTripWorkspace = (): TripWorkspaceState => ({
  days: [],
  locations: [],
  sources: [],
  budget: null,
  validation: null,
  repair: null,
});

export const createInitialTravelPlannerState = (
  options: TravelPlannerInitialOptions,
): TravelPlannerState => ({
  planningStatus: 'idle',
  showMap: options.showMap,
  chatPanelWidthPercent: 70.6,
  mapLocations: [],
  mapSuppressed: false,
  tripWorkspace: createEmptyTripWorkspace(),
  activeTripPlan: null,
  activeTripDocument: null,
  previousTripPlan: null,
  dayRoutes: [],
  activeMapGroupId: '',
  selectedLocationId: '',
  sidePanelMapPercent: options.sidePanelMapPercent,
  mobilePrimaryView: 'chat',
});

export const setTravelPlannerField = <Field extends keyof TravelPlannerState>(
  field: Field,
  value: SetStateAction<TravelPlannerState[Field]>,
): TravelPlannerAction => ({ type: 'field/set', field, value } as SetFieldAction);

export const travelPlannerReducer = (
  state: TravelPlannerState,
  action: TravelPlannerAction,
): TravelPlannerState => {
  if (action.type === 'field/set') {
    const update = action.value as unknown;
    const current = state[action.field] as unknown;
    const next = typeof update === 'function'
      ? (update as (value: unknown) => unknown)(current)
      : update;
    return {
      ...state,
      [action.field]: next,
    };
  }

  if (action.type === 'trip/reset') {
    return {
      ...state,
      planningStatus: 'idle',
      showMap: action.showMap ?? false,
      mapLocations: [],
      mapSuppressed: false,
      tripWorkspace: createEmptyTripWorkspace(),
      activeTripPlan: null,
      activeTripDocument: null,
      previousTripPlan: null,
      dayRoutes: [],
      activeMapGroupId: '',
      selectedLocationId: '',
      mobilePrimaryView: 'chat',
    };
  }

  if (action.type === 'trip/restore') {
    const hasPlan = action.plan !== null;
    return {
      ...state,
      planningStatus: hasPlan ? 'completed' : 'idle',
      showMap: hasPlan && action.locations.length > 0,
      mapLocations: hasPlan ? action.locations : [],
      mapSuppressed: false,
      tripWorkspace: hasPlan ? action.workspace : createEmptyTripWorkspace(),
      activeTripPlan: action.plan,
      activeTripDocument: action.document,
      previousTripPlan: null,
      dayRoutes: [],
      activeMapGroupId: '',
      selectedLocationId: '',
    };
  }

  if (action.type === 'trip/begin-structured-replacement') {
    return {
      ...state,
      mapLocations: [],
      mapSuppressed: false,
      tripWorkspace: createEmptyTripWorkspace(),
      activeTripPlan: null,
      activeTripDocument: null,
      previousTripPlan: null,
      dayRoutes: [],
      activeMapGroupId: '',
      selectedLocationId: '',
    };
  }

  return state;
};
