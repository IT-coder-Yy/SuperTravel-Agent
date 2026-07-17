import { useCallback, useEffect, useState } from 'react';

export type PlanningMode = 'fast_chat' | 'standard_plan' | 'deep_research';

export interface AppSettings {
  planningMode: PlanningMode;
  showMapDefault: boolean;
  useUserProfile: boolean;
  allowWebSearch: boolean;
}

const STORAGE_KEY = 'supertravelagent.appSettings';
const SETTINGS_CHANGE_EVENT = 'supertravelagent:app-settings-change';

export const defaultAppSettings: AppSettings = {
  planningMode: 'standard_plan',
  showMapDefault: false,
  useUserProfile: true,
  allowWebSearch: true,
};

const readSettings = (): AppSettings => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...defaultAppSettings };
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    const planningMode: PlanningMode = ['fast_chat', 'standard_plan', 'deep_research'].includes(
      String(parsed.planningMode)
    )
      ? parsed.planningMode as PlanningMode
      : defaultAppSettings.planningMode;
    return {
      planningMode,
      showMapDefault: typeof parsed.showMapDefault === 'boolean'
        ? parsed.showMapDefault
        : defaultAppSettings.showMapDefault,
      useUserProfile: typeof parsed.useUserProfile === 'boolean'
        ? parsed.useUserProfile
        : defaultAppSettings.useUserProfile,
      allowWebSearch: typeof parsed.allowWebSearch === 'boolean'
        ? parsed.allowWebSearch
        : defaultAppSettings.allowWebSearch,
    };
  } catch (_error) {
    return { ...defaultAppSettings };
  }
};

export const useAppSettings = () => {
  const [settings, setSettingsState] = useState<AppSettings>(() => readSettings());

  const setSettings = useCallback((nextSettings: AppSettings) => {
    const normalized = {
      ...defaultAppSettings,
      ...nextSettings,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
    setSettingsState(normalized);
    window.dispatchEvent(new CustomEvent(SETTINGS_CHANGE_EVENT));
  }, []);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        setSettingsState(readSettings());
      }
    };
    const handleLocalChange = () => setSettingsState(readSettings());
    window.addEventListener('storage', handleStorage);
    window.addEventListener(SETTINGS_CHANGE_EVENT, handleLocalChange);
    return () => {
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener(SETTINGS_CHANGE_EVENT, handleLocalChange);
    };
  }, []);

  return { settings, setSettings };
};
