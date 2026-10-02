import type { LocationPoint } from '../MapComponent';
import { routeLineCoordinates, type DayRouteGeometry } from './routePresentation';

type Point = { lng: number; lat: number };
type Convertor = {
  translate(points: Point[], from: number, to: number,
    callback: (result: { status: number; points?: Point[] }) => void): void;
};

/** 仅转换显示副本；原行程和路线的坐标、版本均不写回。SDK 每批最多 10 点。 */
export async function convertBaiduScene(
  sdk: { Point: new (lng: number, lat: number) => Point; Convertor: new () => Convertor },
  locations: LocationPoint[],
  routes: DayRouteGeometry[],
  cache: Map<string, Point>,
  isCancelled: () => boolean = () => false,
) {
  const key = (lng: number, lat: number) => `${lng},${lat}`;
  const pending = new Map<string, Point>();
  const collect = (lng: number, lat: number) => {
    if (!cache.has(key(lng, lat))) pending.set(key(lng, lat), new sdk.Point(lng, lat));
  };
  locations.forEach(({ lng, lat }) => collect(lng, lat));
  routes.filter((route) => route.coordinate_system === 'WGS84').forEach((route) => {
    route.legs.forEach((leg) => routeLineCoordinates(route, leg).forEach(([lng, lat]) => collect(lng, lat)));
  });
  const entries = [...pending.entries()];
  const convertor = new sdk.Convertor();
  for (let offset = 0; offset < entries.length; offset += 10) {
    if (isCancelled()) throw new Error('BAIDU_CONVERSION_CANCELLED');
    const batch = entries.slice(offset, offset + 10);
    const converted = await new Promise<Point[]>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('BAIDU_CONVERSION_TIMEOUT')), 12000);
      convertor.translate(batch.map(([, point]) => point), 1, 5, (result) => {
        clearTimeout(timer);
        if (result.status !== 0 || result.points?.length !== batch.length
          || result.points.some((point) => !Number.isFinite(point.lng) || !Number.isFinite(point.lat))) {
          reject(new Error('BAIDU_CONVERSION_FAILED'));
        } else resolve(result.points);
      });
    });
    batch.forEach(([id], index) => cache.set(id, converted[index]));
  }
  return {
    locations: locations.map((location) => ({ ...location, ...cache.get(key(location.lng, location.lat))! })),
    routes: routes.map((route): DayRouteGeometry => route.coordinate_system !== 'WGS84' ? route : ({
      ...route, coordinate_system: 'BD09LL',
      legs: route.legs.map((leg) => {
        const coordinates = routeLineCoordinates(route, leg).map(([lng, lat]) => {
          const point = cache.get(key(lng, lat))!;
          return [point.lng, point.lat];
        });
        return { ...leg, coordinate_system: 'BD09LL',
          geometry: coordinates.length >= 2 ? { type: 'LineString', coordinates } : null };
      }),
    })),
  };
}
