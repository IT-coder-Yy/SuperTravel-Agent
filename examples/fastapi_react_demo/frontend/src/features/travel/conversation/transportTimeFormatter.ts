type TransportTimeRecord = Record<string, unknown>;

type TransportScope = 'domestic' | 'international';
type TransportMomentRole = 'departure' | 'arrival';

const isRecord = (value: unknown): value is TransportTimeRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const textAt = (value: unknown, key: string): string => (
  isRecord(value) && typeof value[key] === 'string' ? value[key].trim() : ''
);

const numberAt = (value: unknown, key: string): number | null => (
  isRecord(value) && typeof value[key] === 'number' && Number.isFinite(value[key])
    ? value[key]
    : null
);

const isKnownScope = (value: unknown): value is TransportScope => (
  value === 'domestic' || value === 'international'
);

const dateTimeInZone = (iso: string, timezone: string): string | null => {
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(instant);
    const values = Object.fromEntries(
      parts
        .filter((part) => ['year', 'month', 'day', 'hour', 'minute'].includes(part.type))
        .map((part) => [part.type, part.value]),
    );
    return values.year && values.month && values.day && values.hour && values.minute
      ? `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`
      : null;
  } catch {
    return null;
  }
};

const arrivalDayOffsetHint = (value: unknown, role: TransportMomentRole): string => {
  if (role !== 'arrival') return '';
  const dayOffset = numberAt(value, 'day_offset') || 0;
  if (dayOffset === 1) return '（次日抵达）';
  return dayOffset > 1 ? `（${dayOffset} 日后抵达）` : '';
};

export const formatTransportMoment = (
  value: unknown,
  scope: unknown,
  role: TransportMomentRole,
): string => {
  const prefix = role === 'departure' ? '出发' : '到达';
  const displayText = textAt(value, 'display_text');
  const utc = textAt(value, 'utc');
  const localIso = textAt(value, 'local_iso');
  const timezone = textAt(value, 'timezone');
  const beijingIso = textAt(value, 'beijing_iso');
  const resolvedScope = isKnownScope(scope) ? scope : null;
  const localTime = localIso && timezone ? dateTimeInZone(localIso, timezone) : null;
  const beijingTime = beijingIso ? dateTimeInZone(beijingIso, 'Asia/Shanghai') : null;

  if (utc && localTime && beijingTime && resolvedScope) {
    const base = resolvedScope === 'domestic'
      ? `北京时间 ${beijingTime}`
      : `当地时间 ${localTime}（${timezone}） / 北京时间 ${beijingTime}`;
    return `${prefix}：${base}${arrivalDayOffsetHint(value, role)}`;
  }

  return displayText ? `${prefix}：来源原始时刻 ${displayText}（时区待确认）` : '';
};

export const formatTransportTimePair = (
  departure: unknown,
  arrival: unknown,
  scope: unknown,
): string => [
  formatTransportMoment(departure, scope, 'departure'),
  formatTransportMoment(arrival, scope, 'arrival'),
].filter(Boolean).join('；');
