export type TripWorkspaceActionLocation = {
  name: string;
  day?: number | string;
};

export type TripWorkspaceQuickAction =
  | 'slow_down'
  | 'lower_budget'
  | 'family_friendly'
  | 'hidden_gems'
  | 'reduce_commute';

const updateRequirement = '请直接更新当前旅行规划，不要只提供建议。完成后返回更新后的完整结构化行程，并同步更新日程、地点、预算和校验结果中受影响的部分。';

export const formatTripDay = (value: TripWorkspaceActionLocation['day']) => {
  const text = value === undefined || value === null ? '' : String(value).trim();
  if (!text) return '未分组';
  const match = text.match(/\d+/);
  return match ? `第${match[0]}天` : text;
};

const describeLocation = (location: TripWorkspaceActionLocation) => {
  const day = formatTripDay(location.day);
  return day === '未分组' ? `地点“${location.name}”` : `${day}的地点“${location.name}”`;
};

export const buildReplaceLocationPrompt = (location: TripWorkspaceActionLocation) => (
  `${updateRequirement} 请将${describeLocation(location)}替换为同一区域内更合适的备选地点，保持当天主题和路线连贯，并说明替换原因。`
);

export const buildDeleteLocationPrompt = (location: TripWorkspaceActionLocation) => (
  `${updateRequirement} 请删除${describeLocation(location)}，重新安排当天剩余地点的顺序、时间和交通；除非行程明显过空，否则不要自动添加新地点。`
);

export const buildMoveLocationPrompt = (
  location: TripWorkspaceActionLocation,
  targetDay?: string,
) => {
  const sourceDay = formatTripDay(location.day);
  const sourceDescription = sourceDay === '未分组' ? '当前行程中' : `${sourceDay}`;
  const destination = targetDay
    ? `移到${targetDay}`
    : '移到由你判断更合适的另一天（不能保留在原日期）';
  return `${updateRequirement} 请将${sourceDescription}的地点“${location.name}”${destination}，并重新平衡原日期和目标日期的游览顺序、时间与交通。`;
};

export const quickActionPrompts: Record<TripWorkspaceQuickAction, string> = {
  slow_down: `${updateRequirement} 请放慢整体节奏，减少每天的景点数量，增加休息和机动时间，并优先保留核心体验。`,
  lower_budget: `${updateRequirement} 请降低整体预算，优先替换为更经济的住宿、餐饮、门票和交通方案，同时尽量保留核心体验。`,
  family_friendly: `${updateRequirement} 请增加亲子友好安排，降低长距离步行、连续排队和过晚活动的比例，并补充适合儿童休息与就餐的安排。`,
  hidden_gems: `${updateRequirement} 请增加有可靠依据的小众路线，适当替换过度商业化或高度重复的地点，同时说明选择理由和到访注意事项。`,
  reduce_commute: `${updateRequirement} 请减少跨区域通勤，按地理位置重新串联每天的地点，压缩无效往返，并同步调整游览顺序和交通方式。`,
};

export const buildOpenStreetMapUrl = (latitude: number, longitude: number) => {
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  const lat = latitude.toFixed(6);
  const lng = longitude.toFixed(6);
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=16/${lat}/${lng}`;
};
