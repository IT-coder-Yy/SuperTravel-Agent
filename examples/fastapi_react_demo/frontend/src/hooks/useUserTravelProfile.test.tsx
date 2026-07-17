import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  defaultUserTravelProfile,
  useUserTravelProfile,
  UserTravelProfile,
} from './useUserTravelProfile';

const STORAGE_KEY = 'supertravelagent.userTravelProfile';

describe('useUserTravelProfile', () => {
  it('reads a saved profile from localStorage', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      user_id: 'traveler-1',
      preferred_budget_level: 'mid-range',
      travel_style: ['culture'],
      dietary_preferences: ['vegetarian'],
      disliked_items: [],
      accessibility_needs: [],
    }));

    const { result } = renderHook(() => useUserTravelProfile());

    expect(result.current.profile).toEqual({
      user_id: 'traveler-1',
      preferred_budget_level: 'mid-range',
      travel_style: ['culture'],
      dietary_preferences: ['vegetarian'],
      disliked_items: [],
      accessibility_needs: [],
    });
  });

  it('persists updates and restores them on the next mount', () => {
    const profile: UserTravelProfile = {
      user_id: 'traveler-2',
      travel_style: ['food', 'relaxed'],
      dietary_preferences: [],
      pace: 'slow',
      disliked_items: ['crowds'],
      accessibility_needs: [],
    };
    const firstMount = renderHook(() => useUserTravelProfile());

    act(() => {
      firstMount.result.current.setProfile(profile);
    });

    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}') as UserTravelProfile;
    expect(saved).toMatchObject(profile);
    expect(Number.isNaN(Date.parse(saved.updated_at ?? ''))).toBe(false);

    firstMount.unmount();
    const secondMount = renderHook(() => useUserTravelProfile());
    expect(secondMount.result.current.profile).toEqual(saved);
  });

  it('clears the saved profile', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ user_id: 'traveler-3' }));
    const { result } = renderHook(() => useUserTravelProfile());

    act(() => {
      result.current.clearProfile();
    });

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(result.current.profile).toEqual(defaultUserTravelProfile);
  });
});
