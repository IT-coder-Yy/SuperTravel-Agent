import { useCallback, useEffect, useState } from 'react';

export interface UserTravelProfile {
  user_id: string;
  home_city?: string;
  preferred_budget_level?: string;
  default_people_type?: string;
  travel_style: string[];
  dietary_preferences: string[];
  pace?: string;
  hotel_preference?: string;
  transport_preference?: string;
  disliked_items: string[];
  accessibility_needs: string[];
  updated_at?: string;
}

const STORAGE_KEY = 'supertravelagent.userTravelProfile';
const PROFILE_CHANGE_EVENT = 'supertravelagent:user-travel-profile-change';

export const defaultUserTravelProfile: UserTravelProfile = {
  user_id: 'local',
  travel_style: [],
  dietary_preferences: [],
  disliked_items: [],
  accessibility_needs: [],
};

const optionalString = (value: unknown): string | undefined => {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim();
  return normalized || undefined;
};

const stringList = (value: unknown): string[] => {
  const source = Array.isArray(value) ? value : typeof value === 'string' ? [value] : [];
  return Array.from(
    new Set(
      source
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.trim())
        .filter(Boolean)
    )
  );
};

export const normalizeUserTravelProfile = (value: unknown): UserTravelProfile => {
  const source = value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};

  const profile: UserTravelProfile = {
    user_id: optionalString(source.user_id) || 'local',
    travel_style: stringList(source.travel_style),
    dietary_preferences: stringList(source.dietary_preferences),
    disliked_items: stringList(source.disliked_items),
    accessibility_needs: stringList(source.accessibility_needs),
  };

  const scalarFields = [
    'home_city',
    'preferred_budget_level',
    'default_people_type',
    'pace',
    'hotel_preference',
    'transport_preference',
    'updated_at',
  ] as const;

  scalarFields.forEach((field) => {
    const normalized = optionalString(source[field]);
    if (normalized) profile[field] = normalized;
  });

  return profile;
};

const readProfile = (): UserTravelProfile => {
  if (typeof window === 'undefined') return normalizeUserTravelProfile(undefined);

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return normalizeUserTravelProfile(raw ? JSON.parse(raw) : undefined);
  } catch (_error) {
    return normalizeUserTravelProfile(undefined);
  }
};

export const useUserTravelProfile = () => {
  const [profile, setProfileState] = useState<UserTravelProfile>(() => readProfile());

  const setProfile = useCallback((nextProfile: UserTravelProfile) => {
    const savedProfile = normalizeUserTravelProfile({
      ...nextProfile,
      updated_at: new Date().toISOString(),
    });
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(savedProfile));
    setProfileState(savedProfile);
    window.dispatchEvent(new CustomEvent(PROFILE_CHANGE_EVENT));
  }, []);

  const clearProfile = useCallback(() => {
    window.localStorage.removeItem(STORAGE_KEY);
    const emptyProfile = normalizeUserTravelProfile(undefined);
    setProfileState(emptyProfile);
    window.dispatchEvent(new CustomEvent(PROFILE_CHANGE_EVENT));
  }, []);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) setProfileState(readProfile());
    };
    const handleLocalChange = () => setProfileState(readProfile());

    window.addEventListener('storage', handleStorage);
    window.addEventListener(PROFILE_CHANGE_EVENT, handleLocalChange);
    return () => {
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener(PROFILE_CHANGE_EVENT, handleLocalChange);
    };
  }, []);

  return { profile, setProfile, clearProfile };
};
