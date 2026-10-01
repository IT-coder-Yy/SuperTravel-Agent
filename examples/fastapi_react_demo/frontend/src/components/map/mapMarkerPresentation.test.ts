import { describe, expect, it } from 'vitest';
import {
  clusterMapLocations,
  escapeMapHtml,
  mapAnchorGlyph,
  mapAnchorLabel,
  mapCategoryGlyph,
  mapDayColor,
} from './mapMarkerPresentation';

const closeLocations = [
  { id: 'a', name: '西湖', lat: 30.2501, lng: 120.1501, day: 1, category: '景点' },
  { id: 'b', name: '断桥', lat: 30.2503, lng: 120.1503, day: 1, category: '景点' },
  { id: 'c', name: '灵隐寺', lat: 30.241, lng: 120.101, day: 2, category: '文化体验' },
];

describe('mapMarkerPresentation', () => {
  it('低缩放时聚合相邻地点，放大后保留单个地点', () => {
    const clustered = clusterMapLocations(closeLocations, 10);
    const expanded = clusterMapLocations(closeLocations, 16);

    expect(clustered.some((item) => item.kind === 'cluster' && item.locations.length > 1)).toBe(true);
    expect(expanded).toHaveLength(3);
    expect(expanded.every((item) => item.kind === 'location')).toBe(true);
  });

  it('以日期色和类别图标传达路线与地点语义，并转义动态文本', () => {
    expect(mapDayColor(1)).not.toBe(mapDayColor(2));
    expect(mapCategoryGlyph('交通枢纽')).toBe('↗');
    expect(escapeMapHtml('<西湖 & 断桥>')).toBe('&lt;西湖 &amp; 断桥&gt;');
  });

  it('将住宿与市内交通表达为轻量锚点，而不是活动序号', () => {
    expect(mapAnchorGlyph('arrival_hub')).toBe('↘');
    expect(mapAnchorGlyph('lodging_departure')).toBe('⌂');
    expect(mapAnchorLabel('departure_hub')).toBe('离开锚点');
  });
});
