import { describe, expect, it } from 'vitest';
import {
  normalizeDayRoutes,
  formalDayRoutes,
  routeAvailability,
  routeLineCoordinates,
} from './routePresentation';

const readyRoute = {
  day: 1,
  plan_version: 3,
  provider: 'openrouteservice',
  coordinate_system: 'WGS84',
  status: 'ready',
  legs: [{
    status: 'ready',
    provider: 'openrouteservice',
    coordinate_system: 'WGS84',
    geometry: { type: 'LineString', coordinates: [[120.15, 30.25], [120.1, 30.24]] },
  }],
};

describe('routePresentation', () => {
  it('正式锚点路线按各自提供方展示，缺失接驳段保持待确认', () => {
    const document = { revision: 4, map_guidance: { day_routes: [{ day: 1, status: 'partial', legs: [
      { ...readyRoute.legs[0], from_id: 'arrival', to_id: 'activity' },
      { ...readyRoute.legs[0], from_id: 'activity', to_id: 'hotel', provider: 'baidu', coordinate_system: 'BD09LL' },
      { from_id: 'hotel', to_id: 'departure', status: 'unavailable' },
    ] }] } };
    const routes = formalDayRoutes(document);
    expect(routes.every((route) => route.plan_version === 4)).toBe(true);
    expect(routes.flatMap((route) => route.legs.map((leg) => routeLineCoordinates(route, leg))).filter((points) => points.length >= 2)).toHaveLength(2);
    expect(routeAvailability(routes)).toBe('partial');
    expect(routeAvailability(formalDayRoutes({ revision: 4, map_guidance: { day_routes: [{ day: 2, status: 'ready', legs: [] }] } }))).toBe('unavailable');
  });

  it('只保留匹配提供方和坐标系的有效真实路线', () => {
    const routes = normalizeDayRoutes([readyRoute]);

    expect(routeAvailability(routes)).toBe('ready');
    expect(routeLineCoordinates(routes[0], routes[0].legs[0])).toEqual([
      [120.15, 30.25],
      [120.1, 30.24],
    ]);
  });

  it('拒绝缺点、非法坐标和坐标系不匹配的路线，而不生成直线降级', () => {
    const routes = normalizeDayRoutes([
      {
        ...readyRoute,
        legs: [
          { ...readyRoute.legs[0], geometry: { type: 'LineString', coordinates: [[120.15, 30.25]] } },
          { ...readyRoute.legs[0], coordinate_system: 'BD09LL' },
          { ...readyRoute.legs[0], geometry: { type: 'LineString', coordinates: [[999, 30.25], [120.1, 30.24]] } },
        ],
      },
    ]);

    expect(routes[0].status).toBe('unavailable');
    expect(routeAvailability(routes)).toBe('unavailable');
    expect(routes[0].legs.every((leg) => routeLineCoordinates(routes[0], leg).length === 0)).toBe(true);
  });

  it('真实路段与失败路段混合时只标记为 partial', () => {
    const routes = normalizeDayRoutes([{ ...readyRoute, status: 'partial', legs: [
      readyRoute.legs[0],
      { status: 'unavailable', provider: 'openrouteservice', coordinate_system: 'WGS84', geometry: null },
    ] }]);

    expect(routes[0].status).toBe('partial');
    expect(routeAvailability(routes)).toBe('partial');
  });
});
