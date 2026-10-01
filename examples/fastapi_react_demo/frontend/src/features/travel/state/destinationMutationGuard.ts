export interface DestinationMutation {
  currentDestination: string;
  nextDestination: string;
}

const GENERIC_DESTINATION_TERMS = new Set([
  '旅行',
  '旅游',
  '行程',
  '计划',
  '一下',
  '国内',
  '中国',
]);

const destinationPatterns = [
  /(?:目的地(?:是|为|[:：])?\s*)([\u4e00-\u9fff]{2,12})(?=\s*(?:市|的|[，,。！!？?；;]|$))/u,
  /(?:规划|安排|制定)\s*([\u4e00-\u9fff]{2,12}?)(?=\s*(?:(?:\d+|[一二三四五六七八九十两]+)(?:天|日)|旅行|旅游|行程|游))/u,
  /(?:前往|去|到达|飞往)\s*([\u4e00-\u9fff]{2,12}?)(?=\s*(?:市)?(?:旅行|旅游|行程|游|玩|[，,。！!？?；;]|$))/u,
];

export const normalizeDestinationName = (value: string): string => value
  .trim()
  .replace(/\s+/gu, '')
  .replace(/^(?:中国|国内)/u, '')
  .replace(/(?:特别行政区|自治区|地区|市)$/u, '');

export const extractExplicitDestination = (text: string): string | null => {
  const source = text.trim();
  if (!source) return null;

  for (const pattern of destinationPatterns) {
    const candidate = normalizeDestinationName(pattern.exec(source)?.[1] || '');
    if (candidate.length >= 2 && candidate.length <= 8 && !GENERIC_DESTINATION_TERMS.has(candidate)) {
      return candidate;
    }
  }
  return null;
};

export const detectDestinationMutation = (
  currentDestination: string,
  input: string,
): DestinationMutation | null => {
  const current = normalizeDestinationName(currentDestination);
  const next = extractExplicitDestination(input);
  if (!current || !next || current === next) return null;
  return { currentDestination: current, nextDestination: next };
};
