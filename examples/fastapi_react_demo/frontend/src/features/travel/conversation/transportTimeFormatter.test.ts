import { describe, expect, it } from 'vitest';
import { formatTransportMoment, formatTransportTimePair } from './transportTimeFormatter';

describe('transportTimeFormatter', () => {
  it('pins domestic transport display to Beijing time and marks an overnight arrival', () => {
    expect(formatTransportTimePair(
      {
        display_text: '23:40',
        utc: '2026-08-01T15:40:00Z',
        local_iso: '2026-08-01T23:40:00+08:00',
        timezone: 'Asia/Shanghai',
        beijing_iso: '2026-08-01T23:40:00+08:00',
        day_offset: 0,
      },
      {
        display_text: '00:20',
        utc: '2026-08-01T16:20:00Z',
        local_iso: '2026-08-02T00:20:00+08:00',
        timezone: 'Asia/Shanghai',
        beijing_iso: '2026-08-02T00:20:00+08:00',
        day_offset: 1,
      },
      'domestic',
    )).toBe('出发：北京时间 2026-08-01 23:40；到达：北京时间 2026-08-02 00:20（次日抵达）');
  });

  it('shows international local and Beijing time without using the browser timezone', () => {
    expect(formatTransportTimePair(
      {
        display_text: '09:00',
        utc: '2026-10-01T01:00:00Z',
        local_iso: '2026-10-01T09:00:00+08:00',
        timezone: 'Asia/Shanghai',
        beijing_iso: '2026-10-01T09:00:00+08:00',
      },
      {
        display_text: '14:00',
        utc: '2026-10-01T05:00:00Z',
        local_iso: '2026-10-01T14:00:00+09:00',
        timezone: 'Asia/Tokyo',
        beijing_iso: '2026-10-01T13:00:00+08:00',
      },
      'international',
    )).toBe('出发：当地时间 2026-10-01 09:00（Asia/Shanghai） / 北京时间 2026-10-01 09:00；到达：当地时间 2026-10-01 14:00（Asia/Tokyo） / 北京时间 2026-10-01 13:00');
  });

  it('keeps an unconverted provider clock explicitly pending instead of guessing a timezone', () => {
    expect(formatTransportMoment({ display_text: '09:00' }, 'international', 'departure'))
      .toBe('出发：来源原始时刻 09:00（时区待确认）');
  });
});
