import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppSettings, useAppSettings } from './useAppSettings';

const STORAGE_KEY = 'supertravelagent.appSettings';

describe('useAppSettings', () => {
  it('reads saved settings from localStorage', () => {
    const savedSettings: AppSettings = {
      planningMode: 'deep_research',
      showMapDefault: true,
      useUserProfile: false,
      allowWebSearch: false,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(savedSettings));

    const { result } = renderHook(() => useAppSettings());

    expect(result.current.settings).toEqual(savedSettings);
  });

  it('persists updates and restores them on the next mount', () => {
    const nextSettings: AppSettings = {
      planningMode: 'fast_chat',
      showMapDefault: true,
      useUserProfile: true,
      allowWebSearch: false,
    };
    const firstMount = renderHook(() => useAppSettings());

    act(() => {
      firstMount.result.current.setSettings(nextSettings);
    });

    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual(nextSettings);

    firstMount.unmount();
    const secondMount = renderHook(() => useAppSettings());
    expect(secondMount.result.current.settings).toEqual(nextSettings);
  });
});
