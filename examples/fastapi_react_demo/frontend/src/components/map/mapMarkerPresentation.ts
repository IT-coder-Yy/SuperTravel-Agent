export type MapAnchorKind = 'lodging_departure' | 'lodging_return' | 'arrival_hub' | 'departure_hub';

export interface MapMarkerLocation {
  id: string;
  name: string;
  lat: number;
  lng: number;
  day?: number | string;
  category?: string;
  anchor_kind?: MapAnchorKind;
  anchor_label?: string;
}

export interface MapLocationCluster {
  kind: 'cluster';
  id: string;
  lat: number;
  lng: number;
  locations: MapMarkerLocation[];
}

export interface MapLocationMarker {
  kind: 'location';
  location: MapMarkerLocation;
}

export type MapMarkerDisplayItem = MapLocationCluster | MapLocationMarker;

const DAY_COLORS = ['#245e56', '#3567cb', '#936337', '#536c4e', '#805d77'];

const categoryIcon = (category: string) => {
  const normalized = category.toLocaleLowerCase();
  if (/(交通|transport|station|airport|hub)/.test(normalized)) return { glyph: '↗', label: '交通' };
  if (/(酒店|住宿|hotel|lodging)/.test(normalized)) return { glyph: '⌂', label: '住宿' };
  if (/(餐|food|restaurant|cafe)/.test(normalized)) return { glyph: '●', label: '餐饮' };
  if (/(购物|shopping)/.test(normalized)) return { glyph: '◇', label: '购物' };
  if (/(娱乐|entertainment|亲子)/.test(normalized)) return { glyph: '★', label: '娱乐' };
  if (/(文化|人文|历史|museum|temple|shrine)/.test(normalized)) return { glyph: '◆', label: '文化地点' };
  if (/(景点|attraction|scenic|sight)/.test(normalized)) return { glyph: '●', label: '景点' };
  return { glyph: '●', label: '地点' };
};

export const mapDayColor = (day: MapMarkerLocation['day']): string => {
  const normalizedDay = Number(String(day ?? '').match(/\d+/)?.[0]);
  return Number.isInteger(normalizedDay) && normalizedDay > 0
    ? DAY_COLORS[(normalizedDay - 1) % DAY_COLORS.length]
    : '#245e56';
};

export const mapCategoryGlyph = (category?: string): string => categoryIcon(category || '').glyph;

export const mapCategoryLabel = (category?: string): string => categoryIcon(category || '').label;

export const mapAnchorGlyph = (kind?: MapAnchorKind): string => {
  if (kind === 'arrival_hub') return '↘';
  if (kind === 'departure_hub') return '↗';
  return '⌂';
};

export const mapAnchorLabel = (kind?: MapAnchorKind): string => {
  if (kind === 'arrival_hub') return '抵达锚点';
  if (kind === 'departure_hub') return '离开锚点';
  if (kind === 'lodging_return') return '返回规划住宿';
  return '从规划住宿出发';
};

export const escapeMapHtml = (value: string): string => value
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#039;');

const clusterCellSize = (zoom: number, count: number): number | null => {
  if (zoom <= 10) return 0.16;
  if (zoom <= 12) return 0.04;
  if (count >= 8 && zoom <= 14) return 0.008;
  return null;
};

/** 轻量网格聚合：低缩放或密集地点时收拢，放大后始终回到单个 Marker。 */
export const clusterMapLocations = (
  locations: MapMarkerLocation[],
  zoom: number,
): MapMarkerDisplayItem[] => {
  const cellSize = clusterCellSize(zoom, locations.length);
  if (!cellSize || locations.length < 2) {
    return locations.map((location) => ({ kind: 'location', location }));
  }

  const cells = new Map<string, MapMarkerLocation[]>();
  locations.forEach((location) => {
    const cellKey = `${Math.floor(location.lat / cellSize)}:${Math.floor(location.lng / cellSize)}`;
    cells.set(cellKey, [...(cells.get(cellKey) || []), location]);
  });

  return Array.from(cells.entries()).reduce<MapMarkerDisplayItem[]>((displayItems, [cellKey, cellLocations]) => {
    if (cellLocations.length === 1) {
      displayItems.push({ kind: 'location', location: cellLocations[0] });
      return displayItems;
    }
    const lat = cellLocations.reduce((total, location) => total + location.lat, 0) / cellLocations.length;
    const lng = cellLocations.reduce((total, location) => total + location.lng, 0) / cellLocations.length;
    displayItems.push({
      kind: 'cluster',
      id: `cluster:${cellKey}:${cellLocations.map((location) => location.id).sort().join(',')}`,
      lat,
      lng,
      locations: cellLocations,
    });
    return displayItems;
  }, []);
};
