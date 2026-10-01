export type RouteCoordinateSystem = 'BD09LL' | 'WGS84';
export type RouteStatus = 'ready' | 'partial' | 'unavailable';

export interface DayRouteGeometry {
  day: number;
  plan_version: number;
  provider?: string;
  status: RouteStatus;
  coordinate_system: RouteCoordinateSystem;
  legs: Array<{
    status: 'ready' | 'unavailable';
    provider?: string;
    coordinate_system?: RouteCoordinateSystem;
    geometry?: { type: 'LineString'; coordinates: number[][] } | null;
  }>;
}

type UnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is UnknownRecord => (
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)
);

const isCoordinateSystem = (value: unknown): value is RouteCoordinateSystem => (
  value === 'BD09LL' || value === 'WGS84'
);

const isFiniteCoordinate = (value: unknown, minimum: number, maximum: number): value is number => (
  typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
);

const normalizeGeometry = (value: unknown): { type: 'LineString'; coordinates: number[][] } | null => {
  if (!isRecord(value) || value.type !== 'LineString' || !Array.isArray(value.coordinates)) return null;
  const coordinates = value.coordinates.map((point) => {
    if (!Array.isArray(point) || point.length < 2) return null;
    const [lng, lat] = point;
    if (!isFiniteCoordinate(lng, -180, 180) || !isFiniteCoordinate(lat, -90, 90)) return null;
    return [lng, lat];
  });
  return coordinates.length >= 2 && coordinates.every((point) => point !== null)
    ? { type: 'LineString', coordinates: coordinates as number[][] }
    : null;
};

/** Drop malformed API legs instead of letting a visual line imply a route that was never verified. */
export const normalizeDayRoutes = (value: unknown): DayRouteGeometry[] => {
  if (!Array.isArray(value)) return [];

  return value.flatMap((rawRoute) => {
    if (!isRecord(rawRoute)) return [];
    const day = rawRoute.day;
    const planVersion = rawRoute.plan_version;
    const provider = typeof rawRoute.provider === 'string' ? rawRoute.provider.trim() : '';
    const coordinateSystem = rawRoute.coordinate_system;
    const routeStatus = rawRoute.status;
    if (
      typeof day !== 'number' || !Number.isInteger(day) || day < 1
      || typeof planVersion !== 'number' || !Number.isInteger(planVersion) || planVersion < 1
      || !provider
      || !isCoordinateSystem(coordinateSystem)
      || !['ready', 'partial', 'unavailable'].includes(String(routeStatus))
    ) return [];

    const legs = Array.isArray(rawRoute.legs) ? rawRoute.legs.flatMap((rawLeg) => {
      if (!isRecord(rawLeg)) return [];
      const geometry = normalizeGeometry(rawLeg.geometry);
      const ready = routeStatus !== 'unavailable'
        && rawLeg.status === 'ready'
        && rawLeg.provider === provider
        && rawLeg.coordinate_system === coordinateSystem
        && geometry !== null;
      return [{
        status: ready ? 'ready' as const : 'unavailable' as const,
        provider: ready ? provider : undefined,
        coordinate_system: ready ? coordinateSystem : undefined,
        geometry: ready ? geometry : null,
      }];
    }) : [];
    const readyCount = legs.filter((leg) => leg.status === 'ready').length;
    const status: RouteStatus = readyCount === 0
      ? 'unavailable'
      : readyCount === legs.length ? 'ready' : 'partial';

    return [{
      day,
      plan_version: planVersion,
      provider,
      status,
      coordinate_system: coordinateSystem,
      legs,
    }];
  });
};

/** V3 正式路线已由服务端核验，按提供方分组保留锚点路段，不另发活动限定查询。 */
export const formalDayRoutes = (document: { revision: number; map_guidance: { day_routes: Array<{ day: number; status: string; legs: Array<{ provider?: string | null; coordinate_system?: string | null; status: string }> }> } }): DayRouteGeometry[] => (
  document.map_guidance.day_routes.flatMap((day) => {
    const groups = new Map<string, typeof day.legs>();
    for (const leg of day.legs) {
      const key = `${leg.provider || 'unavailable'}:${leg.coordinate_system || 'WGS84'}`;
      groups.set(key, [...(groups.get(key) || []), leg]);
    }
    if (!groups.size) groups.set('unavailable:WGS84', []);
    return Array.from(groups.entries()).flatMap(([key, legs]) => {
      const [provider, coordinateSystem] = key.split(':');
      return normalizeDayRoutes([{ day: day.day, plan_version: document.revision, provider,
        coordinate_system: coordinateSystem, status: day.status === 'ready' ? 'ready' : 'partial', legs }]);
    });
  })
);

export const routeLineCoordinates = (
  route: DayRouteGeometry,
  leg: DayRouteGeometry['legs'][number],
): Array<[number, number]> => {
  if (
    route.status === 'unavailable'
    || !route.provider
    || leg.status !== 'ready'
    || leg.provider !== route.provider
    || leg.coordinate_system !== route.coordinate_system
    || !leg.geometry
  ) return [];
  const geometry = normalizeGeometry(leg.geometry);
  return geometry ? geometry.coordinates.map(([lng, lat]) => [lng, lat]) : [];
};

export const routeAvailability = (routes: DayRouteGeometry[]): RouteStatus => {
  const hasRealGeometry = routes.some((route) => route.legs.some(
    (leg) => routeLineCoordinates(route, leg).length >= 2,
  ));
  if (!hasRealGeometry) return 'unavailable';
  const hasUnavailableSegment = routes.some((route) => (
    route.status !== 'ready'
    || route.legs.some((leg) => routeLineCoordinates(route, leg).length < 2)
  ));
  return hasUnavailableSegment ? 'partial' : 'ready';
};

export const routeAvailabilityMessage = (status: RouteStatus): string => (
  status === 'partial'
    ? '部分真实路线暂不可用，当前仅显示已核验路段'
    : '真实路线暂不可用，当前仅显示日程地点'
);
