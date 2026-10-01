import dayjs, { type Dayjs } from 'dayjs';
import type { TripCreateRequest } from './tripCreateTypes';

export const MIN_TRIP_DAYS = 1;
export const MAX_TRIP_DAYS = 7;

export interface TripCreateValidationResult {
  valid: boolean;
  errors: Partial<Record<keyof TripCreateRequest | 'travelers', string>>;
}

const normalizedText = (value: string) => value.trim();

export const tripDaysInclusive = (startDate: string, endDate: string): number => {
  const start = dayjs(startDate);
  const end = dayjs(endDate);
  if (!start.isValid() || !end.isValid()) return 0;
  return end.startOf('day').diff(start.startOf('day'), 'day') + 1;
};

export const validateTripCreateRequest = (
  request: TripCreateRequest,
  today: Dayjs = dayjs(),
): TripCreateValidationResult => {
  const errors: TripCreateValidationResult['errors'] = {};
  const origin = normalizedText(request.origin);
  const destination = normalizedText(request.destination);
  const start = dayjs(request.startDate);
  const end = dayjs(request.endDate);
  const days = tripDaysInclusive(request.startDate, request.endDate);
  const travelers = request.adults + request.children + request.seniors;

  if (!origin) errors.origin = '请输入出发地';
  if (!destination) errors.destination = '请输入目的地';
  if (origin && destination && origin === destination) {
    errors.destination = '目的地需要与出发地不同';
  }
  if (!start.isValid() || !end.isValid()) {
    errors.startDate = '请选择明确的出发和返程日期';
  } else if (start.startOf('day').isBefore(today.startOf('day'))) {
    errors.startDate = '出发日期不能早于今天';
  } else if (days < MIN_TRIP_DAYS || days > MAX_TRIP_DAYS) {
    errors.endDate = `行程只支持 ${MIN_TRIP_DAYS}～${MAX_TRIP_DAYS} 天`;
  }
  if ([request.adults, request.children, request.seniors].some((value) => value < 0)) {
    errors.travelers = '人数不能小于 0';
  } else if (travelers < 1) {
    errors.travelers = '至少需要 1 位出行人';
  }
  if (!Number.isFinite(request.budget) || request.budget <= 0) {
    errors.budget = '请输入大于 0 元的总预算';
  }

  return { valid: Object.keys(errors).length === 0, errors };
};

export const buildTripCreationPrompt = (request: TripCreateRequest): string => {
  const days = tripDaysInclusive(request.startDate, request.endDate);
  const preferences = request.preferences.length > 0
    ? request.preferences.join('、')
    : '均衡安排';
  const partyType = request.partyType ? `同行关系：${request.partyType}。` : '';
  return [
    `请帮我规划一趟从${request.origin.trim()}出发、前往${request.destination.trim()}的${days}天旅行。`,
    `日期为${request.startDate}至${request.endDate}。`,
    `出行人员：成人${request.adults}人、儿童${request.children}人、老人${request.seniors}人。`,
    partyType,
    `总预算${Math.round(request.budget)}元，旅行偏好：${preferences}。`,
    '请生成完整行程，并对强实时交通和地图数据进行真实核验。',
  ].join('');
};
