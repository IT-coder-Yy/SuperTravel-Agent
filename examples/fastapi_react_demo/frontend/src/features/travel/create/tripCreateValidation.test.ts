import dayjs from 'dayjs';
import { describe, expect, it } from 'vitest';
import {
  buildTripCreationPrompt,
  tripDaysInclusive,
  validateTripCreateRequest,
} from './tripCreateValidation';
import type { TripCreateRequest } from './tripCreateTypes';

const baseRequest: TripCreateRequest = {
  origin: '上海',
  destination: '杭州',
  startDate: '2026-08-15',
  endDate: '2026-08-17',
  adults: 2,
  children: 0,
  seniors: 0,
  budget: 6000,
  partyType: '情侣',
  preferences: ['人文历史', '当地美食'],
};

describe('tripCreateValidation', () => {
  it('counts an inclusive same-day trip as one day', () => {
    expect(tripDaysInclusive('2026-08-15', '2026-08-15')).toBe(1);
    expect(tripDaysInclusive('2026-08-15', '2026-08-17')).toBe(3);
  });

  it('accepts a complete one-to-seven-day request including today', () => {
    const result = validateTripCreateRequest(baseRequest, dayjs('2026-08-15'));
    expect(result).toEqual({ valid: true, errors: {} });
  });

  it('rejects past dates, over-seven-day ranges and an empty traveler group', () => {
    const result = validateTripCreateRequest({
      ...baseRequest,
      startDate: '2026-08-14',
      endDate: '2026-08-22',
      adults: 0,
    }, dayjs('2026-08-15'));

    expect(result.valid).toBe(false);
    expect(result.errors.startDate).toBe('出发日期不能早于今天');
    expect(result.errors.travelers).toBe('至少需要 1 位出行人');
  });

  it('builds one complete deterministic prompt for the existing planning stream', () => {
    const prompt = buildTripCreationPrompt(baseRequest);

    expect(prompt).toContain('从上海出发、前往杭州的3天旅行');
    expect(prompt).toContain('成人2人、儿童0人、老人0人');
    expect(prompt).toContain('同行关系：情侣');
    expect(prompt).toContain('总预算6000元');
    expect(prompt).toContain('人文历史、当地美食');
  });
});
