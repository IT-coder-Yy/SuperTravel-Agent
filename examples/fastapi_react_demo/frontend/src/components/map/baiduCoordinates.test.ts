import { expect, it, vi } from 'vitest';
import { convertBaiduScene } from './baiduCoordinates';
import type { DayRouteGeometry } from './routePresentation';

it('按 10 点分批转换，复用重复坐标，不修改原行程和路线', async () => {
  class Point { constructor(public lng: number, public lat: number) {} }
  const translate = vi.fn((points, _from, _to, done) => done({ status: 0,
    points: points.map((point: Point) => ({ lng: point.lng + 0.01, lat: point.lat + 0.01 })) }));
  class Convertor { translate = translate; }
  const locations = Array.from({ length: 12 }, (_, index) => ({ id: String(index), name: '地点', lng: 120 + index / 100, lat: 30 }));
  const routes: DayRouteGeometry[] = [{ day: 1, plan_version: 3, provider: 'verified', status: 'ready', coordinate_system: 'WGS84',
    legs: [{ status: 'ready', provider: 'verified', coordinate_system: 'WGS84', geometry: { type: 'LineString', coordinates: [[120, 30], [120.01, 30]] } }] }];
  const snapshot = JSON.stringify({ locations, routes });
  const cache = new Map();
  const result = await convertBaiduScene({ Point, Convertor }, locations, routes, cache);
  expect(translate.mock.calls.map((call) => call[0].length)).toEqual([10, 2]);
  expect(translate.mock.calls[0].slice(1, 3)).toEqual([1, 5]);
  expect(result.locations[0].lng).toBe(120.01);
  expect(result.routes[0].legs[0].geometry?.coordinates[0]).toEqual([120.01, 30.01]);
  expect(result.routes[0].coordinate_system).toBe('BD09LL');
  expect(result.routes[0].plan_version).toBe(3);
  expect(JSON.stringify({ locations, routes })).toBe(snapshot);
  await convertBaiduScene({ Point, Convertor }, locations, routes, cache);
  expect(translate).toHaveBeenCalledTimes(2);
});

it('转换失败时不使用原坐标冒充百度坐标', async () => {
  class Point { constructor(public lng: number, public lat: number) {} }
  class Convertor { translate = (_points: unknown, _from: number, _to: number, done: any) => done({ status: 1 }); }
  await expect(convertBaiduScene({ Point, Convertor }, [{ id: 'a', name: '地点', lng: 120, lat: 30 }], [], new Map()))
    .rejects.toThrow('BAIDU_CONVERSION_FAILED');
});
