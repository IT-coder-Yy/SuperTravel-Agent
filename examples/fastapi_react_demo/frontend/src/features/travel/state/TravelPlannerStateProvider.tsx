import { createContext, useContext, useEffect, useMemo, useReducer, type Dispatch, type ReactNode } from 'react';
import { useAppSettings } from '../../../hooks/useAppSettings';
import {
  createInitialTravelPlannerState,
  travelPlannerReducer,
  type TravelPlannerAction,
  type TravelPlannerState,
} from './travelPlannerReducer';

const SIDE_PANEL_MAP_PERCENT_STORAGE_KEY = 'supertravelagent.sidePanelMapPercent';

const readSidePanelMapPercent = (): number => {
  const saved = Number(window.localStorage.getItem(SIDE_PANEL_MAP_PERCENT_STORAGE_KEY));
  return Number.isFinite(saved) ? Math.min(65, Math.max(55, saved)) : 60;
};

interface TravelPlannerStateContextValue {
  state: TravelPlannerState;
  dispatch: Dispatch<TravelPlannerAction>;
}

const TravelPlannerStateContext = createContext<TravelPlannerStateContextValue | null>(null);

export const TravelPlannerStateProvider = ({ children }: { children: ReactNode }) => {
  const { settings } = useAppSettings();
  const [state, dispatch] = useReducer(
    travelPlannerReducer,
    {
      showMap: settings.showMapDefault,
      sidePanelMapPercent: readSidePanelMapPercent(),
    },
    createInitialTravelPlannerState,
  );

  useEffect(() => {
    window.localStorage.setItem(SIDE_PANEL_MAP_PERCENT_STORAGE_KEY, state.sidePanelMapPercent.toFixed(2));
  }, [state.sidePanelMapPercent]);

  const value = useMemo(() => ({ state, dispatch }), [state]);
  return (
    <TravelPlannerStateContext.Provider value={value}>
      {children}
    </TravelPlannerStateContext.Provider>
  );
};

export const useTravelPlannerState = (): TravelPlannerStateContextValue => {
  const context = useContext(TravelPlannerStateContext);
  if (!context) {
    throw new Error('useTravelPlannerState 必须在 TravelPlannerStateProvider 内使用');
  }
  return context;
};
