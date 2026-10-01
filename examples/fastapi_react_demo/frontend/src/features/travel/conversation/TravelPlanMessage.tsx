import { Fragment, useEffect, useId, useMemo, useState } from 'react';
import type { TravelPlanDocumentV3 } from '../state/travelPlannerTypes';
import { displayableImageAssets, imageDisplayEligibility, toDisplayableImageAsset, TravelImageAsset, TravelImageWithFallback } from './TravelImageAsset';
import { formatTransportTimePair } from './transportTimeFormatter';

export const travelPlanSectionOrder = [
  'destination_overview',
  'outbound_transport',
  'lodging_plan',
  'itinerary',
  'budget_and_reminders',
  'map_guidance',
  'delivery',
  'sources',
] as const;

export type TravelPlanSectionName = (typeof travelPlanSectionOrder)[number];

type TravelPlanRecord = Record<string, unknown>;

interface CitationEntry {
  sourceId: string;
  number: number;
  source: TravelPlanRecord;
  targetId: string;
}

interface CitationContext {
  entriesBySourceId: Map<string, CitationEntry>;
  onCitationClick: (sourceId: string) => void;
}

export interface TravelPlanSectionMap {
  destination_overview: TravelPlanRecord;
  outbound_transport: TravelPlanRecord;
  lodging_plan: TravelPlanRecord;
  itinerary: TravelPlanRecord;
  budget_and_reminders: TravelPlanRecord;
  map_guidance: TravelPlanRecord;
  delivery: TravelPlanRecord;
  sources: TravelPlanRecord;
}

export interface TravelPlanMessageProps {
  tripId?: string;
  planId: string;
  revision: number;
  sections?: Partial<TravelPlanSectionMap>;
  completed: boolean;
  document?: TravelPlanDocumentV3 | null;
}

type DeliveryFormat = 'markdown' | 'pdf';

const isRecord = (value: unknown): value is TravelPlanRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const recordAt = (value: unknown, key: string): TravelPlanRecord => (
  isRecord(value) && isRecord(value[key]) ? value[key] : {}
);

const listAt = (value: unknown, key: string): unknown[] => (
  isRecord(value) && Array.isArray(value[key]) ? value[key] : []
);

const textAt = (value: unknown, key: string): string => (
  isRecord(value) && typeof value[key] === 'string' ? value[key].trim() : ''
);

const numberAt = (value: unknown, key: string): number | null => {
  if (!isRecord(value) || typeof value[key] !== 'number' || !Number.isFinite(value[key])) return null;
  return value[key];
};

const asText = (value: unknown, fallback = ''): string => (
  typeof value === 'string' ? value.trim() || fallback : fallback
);

const referenceIds = (...values: unknown[]): string[] => Array.from(new Set(
  values
    .flatMap((value) => Array.isArray(value) ? value : [value])
    .map((value) => asText(value))
    .filter(Boolean),
));

const sourceStatusLabel = (status: unknown): string => {
  const labels: Record<string, string> = {
    realtime_verified: '实时已核验',
    official_reference: '官方参考',
    map_reference: '地图参考',
    guide_reference: '攻略参考',
    user_confirmation: '待用户确认',
  };
  return labels[asText(status)] || '参考资料';
};

const CitationMarkers = ({
  sourceRefs,
  context,
}: {
  sourceRefs: string[];
  context: CitationContext;
}) => {
  const citations = sourceRefs
    .map((sourceId) => context.entriesBySourceId.get(sourceId))
    .filter((entry): entry is CitationEntry => Boolean(entry));

  if (citations.length === 0) return null;

  return (
    <sup className="travel-plan-citations" aria-label={`参考来源 ${citations.map((entry) => entry.number).join('、')}`}>
      {citations.map((entry, index) => (
        <Fragment key={entry.sourceId}>
          {index > 0 && <span aria-hidden="true">,</span>}
          <button
            type="button"
            className="travel-plan-citation"
            aria-label={`查看参考来源 ${entry.number}`}
            onClick={() => context.onCitationClick(entry.sourceId)}
          >
            {entry.number}
          </button>
        </Fragment>
      ))}
    </sup>
  );
};

const formatMoney = (value: unknown): string => {
  if (!isRecord(value)) return '待确认';
  const amount = numberAt(value, 'amount');
  if (amount === null) return '待确认';
  const currency = textAt(value, 'currency') || 'CNY';
  if (currency === 'CNY') return `¥${Math.round(amount).toLocaleString('zh-CN')}`;
  const original = `${currency} ${amount.toLocaleString('zh-CN')}`;
  const cnyReference = numberAt(value, 'cny_reference_amount');
  if (cnyReference === null) return `${original}（人民币参考待确认）`;
  const exchangeRateDate = textAt(value, 'exchange_rate_as_of');
  return `${original}（约¥${Math.round(cnyReference).toLocaleString('zh-CN')}${exchangeRateDate ? `，汇率参考日期 ${exchangeRateDate}` : ''}）`;
};

const transportModeLabel = (mode: unknown): string => {
  const labels: Record<string, string> = {
    train: '火车',
    intercity_bus: '长途巴士',
    flight: '航班',
  };
  return labels[asText(mode)] || '交通方案';
};

const routeModeLabel = (mode: unknown): string => {
  const labels: Record<string, string> = {
    walk: '步行',
    walking: '步行',
    transit: '公共交通',
    bus: '公交',
    subway: '地铁',
    taxi: '出租车',
    drive: '驾车',
    cycling: '骑行',
  };
  const normalized = asText(mode).toLowerCase();
  return labels[normalized] || asText(mode) || '待路线核验';
};

const seatAvailabilityLabel = (availability: unknown): string => {
  const labels: Record<string, string> = {
    available: '有余票',
    limited: '余票紧张',
    unavailable: '无余票',
    unknown: '余票待确认',
  };
  return labels[asText(availability)] || '余票待确认';
};

const placeCategoryLabel = (category: unknown): string => {
  const normalized = asText(category).toLowerCase();
  const labels: Record<string, string> = {
    attraction: '景点',
    scenic: '景点',
    food: '吃喝',
    restaurant: '吃喝',
    dining: '吃喝',
    transport: '交通',
    hotel: '住宿',
    lodging: '住宿',
    shopping: '购物',
    rest: '休息/自由活动',
    other: '其他',
    '景点': '景点',
    '餐厅': '吃喝',
    '美食': '吃喝',
    '酒店': '住宿',
    '住宿': '住宿',
  };
  return labels[normalized] || asText(category);
};

const sectionStatusText = (section: unknown): string => (
  textAt(section, 'status_reason') || '暂无可展示的详细信息。'
);

const documentSections = (document: TravelPlanDocumentV3): TravelPlanSectionMap => ({
  destination_overview: {
    title: document.title,
    intent: document.intent,
    destination_overview: document.destination_overview,
  },
  outbound_transport: {
    outbound_transport: document.outbound_transport,
    return_transport: document.return_transport,
  },
  lodging_plan: document.lodging_plan as unknown as TravelPlanRecord,
  itinerary: {
    ...document.itinerary,
    notes: document.notes,
  } as unknown as TravelPlanRecord,
  budget_and_reminders: {
    budget: document.budget,
    candidate_pool: document.candidate_pool,
    action_items: document.action_items,
    notes: document.notes.filter((note) => note.scope === 'trip'),
  },
  map_guidance: document.map_guidance as unknown as TravelPlanRecord,
  delivery: document.delivery,
  sources: { sources: document.sources },
});

const renderDestinationOverview = (section: TravelPlanRecord, citations: CitationContext) => {
  const overview = recordAt(section, 'destination_overview');
  const intent = recordAt(section, 'intent');
  const title = textAt(section, 'title');
  const destination = textAt(overview, 'name_zh') || textAt(intent, 'destination') || '目的地待确认';
  const origin = textAt(intent, 'origin');
  const isFlexible = textAt(intent, 'date_mode') === 'flexible';
  const days = numberAt(intent, 'days');
  const startDate = textAt(intent, 'start_date');
  const endDate = textAt(intent, 'end_date');
  const themes = listAt(overview, 'themes').map((theme) => asText(theme)).filter(Boolean);
  const coverImage = toDisplayableImageAsset(overview.cover_image);
  const coverImageState = coverImage ? 'eligible' : imageDisplayEligibility(overview.cover_image);

  return (
    <section data-plan-section="destination_overview" data-cover-image-state={coverImageState} aria-labelledby="travel-plan-overview-heading">
      <h3 id="travel-plan-overview-heading">目的地概览</h3>
      {title && <p><strong>{title}</strong></p>}
      <p>
        {origin ? `${origin} → ` : ''}{destination}
        {days ? ` · ${days} 天` : ''}
        {isFlexible ? ' · 日期待定' : startDate && endDate ? ` · ${startDate} 至 ${endDate}` : ''}
      </p>
      {textAt(overview, 'summary') && (
        <p>
          {textAt(overview, 'summary')}
          <CitationMarkers sourceRefs={referenceIds(listAt(overview, 'evidence_refs'))} context={citations} />
        </p>
      )}
      {coverImage && <TravelImageAsset image={coverImage} className="travel-plan-image--cover" imageLabel={`${destination}目的地封面`} />}
      {themes.length > 0 && <p>旅行主题：{themes.join('、')}</p>}
    </section>
  );
};

const renderTransportOptions = (
  label: string,
  section: TravelPlanRecord,
  isFlexible: boolean,
  citations: CitationContext,
) => {
  const options = listAt(section, 'options').filter(isRecord);
  const selectedOptionId = textAt(section, 'selected_option_id');
  if (isFlexible) {
    return <p>{label}：日期待定，暂不展示具体班次；确定日期后可再核验。</p>;
  }
  if (options.length === 0) {
    const officialQueryUrl = textAt(section, 'official_query_url');
    return (
      <p>
        {label}：{sectionStatusText(section)}
        {officialQueryUrl && (
          <>
            {' '}<a href={officialQueryUrl} target="_blank" rel="noreferrer">前往官方查询</a>
          </>
        )}
      </p>
    );
  }

  const primary = options.find((option) => textAt(option, 'option_id') === selectedOptionId) || options[0];
  const alternatives = options.filter((option) => option !== primary).slice(0, 2);
  const renderOption = (option: TravelPlanRecord, index: number) => {
          const optionId = textAt(option, 'option_id');
          const isPrimary = selectedOptionId ? optionId === selectedOptionId : index === 0;
          const departure = textAt(option, 'departure_place') || '出发地待确认';
          const arrival = textAt(option, 'arrival_place') || '目的地待确认';
          const transportTimes = formatTransportTimePair(
            recordAt(option, 'departure_time'),
            recordAt(option, 'arrival_time'),
            textAt(section, 'scope'),
          );
          const serviceNumber = textAt(option, 'service_number');
          const detail = [
            isPrimary ? '规划采用，未购票' : `备选 ${index}`,
            transportModeLabel(option.mode),
            serviceNumber,
            `${departure} → ${arrival}`,
            transportTimes,
            isRecord(option.price) ? formatMoney(option.price) : '',
          ].filter(Boolean).join(' · ');
          const seatOptions = listAt(option, 'seat_options').filter(isRecord);
          return (
            <li key={optionId || `${label}-${index}`}>
              <div>
                {detail}
                <CitationMarkers sourceRefs={referenceIds(listAt(option, 'source_refs'))} context={citations} />
              </div>
              {seatOptions.length > 0 && (
                <ul className="travel-plan-seat-options" aria-label={`${serviceNumber || transportModeLabel(option.mode)} 可选座席`}>
                  {seatOptions.map((seat, seatIndex) => {
                    const remaining = textAt(seat, 'remaining_text');
                    const price = isRecord(seat.price) ? formatMoney(seat.price) : '';
                    return (
                      <li key={`${textAt(seat, 'name') || 'seat'}-${seatIndex}`}>
                        <strong>{textAt(seat, 'name') || '座席待确认'}</strong>
                        <span>{[remaining || seatAvailabilityLabel(seat.availability), price].filter(Boolean).join(' · ')}</span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
  };
  return <section aria-label={label}><h4>{label}</h4><ul>{renderOption(primary, 0)}</ul>
    {alternatives.length > 0 && <details><summary>其他{label}方案（{alternatives.length}）</summary>
      <ul>{alternatives.map((option, index) => renderOption(option, index + 1))}</ul>
    </details>}
  </section>;
};

const renderOutboundTransport = (
  section: TravelPlanRecord,
  isFlexible: boolean,
  citations: CitationContext,
) => {
  const outbound = recordAt(section, 'outbound_transport');
  const returnTransport = recordAt(section, 'return_transport');

  return (
    <section data-plan-section="outbound_transport" aria-labelledby="travel-plan-transport-heading">
      <h3 id="travel-plan-transport-heading">交通建议</h3>
      {renderTransportOptions('去程交通', outbound, isFlexible, citations)}
      {renderTransportOptions('返程交通', returnTransport, isFlexible, citations)}
    </section>
  );
};

const renderLodgingPlan = (section: TravelPlanRecord, citations: CitationContext) => {
  const options = listAt(section, 'options').filter(isRecord);
  const selectedId = textAt(section, 'planning_lodging_id');
  const selectedFirst = [...options].sort((left, right) => (
    Number(textAt(right, 'lodging_id') === selectedId) - Number(textAt(left, 'lodging_id') === selectedId)
  ));

  return (
    <section data-plan-section="lodging_plan" aria-labelledby="travel-plan-lodging-heading">
      <h3 id="travel-plan-lodging-heading">住宿建议</h3>
      {selectedFirst.length === 0 ? (
        <p>{sectionStatusText(section)}</p>
      ) : (
        <ul>
          {selectedFirst.slice(0, 3).map((option, index) => {
            const area = textAt(option, 'area');
            const rating = numberAt(option, 'rating');
            const reasons = listAt(option, 'reasons').map((reason) => asText(reason)).filter(Boolean);
            return (
              <li key={textAt(option, 'lodging_id') || `lodging-${index}`}>
                <strong>{textAt(option, 'name') || '住宿待确认'}</strong>
                {area ? ` · ${area}` : ''}
                {rating !== null ? ` · 评分 ${rating.toFixed(1)}` : ''}
                {isRecord(option.nightly_price) ? ` · 每晚约 ${formatMoney(option.nightly_price)}` : ''}
                {reasons.length > 0 ? ` · ${reasons.join('；')}` : ''}
                <CitationMarkers sourceRefs={referenceIds(listAt(option, 'source_refs'))} context={citations} />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

const renderItinerary = (section: TravelPlanRecord, citations: CitationContext) => {
  const days = listAt(section, 'days').filter(isRecord);
  const notesById = new Map(
    listAt(section, 'notes')
      .filter(isRecord)
      .map((note) => [textAt(note, 'note_id'), textAt(note, 'content')]),
  );

  const durationText = (activity: TravelPlanRecord): string => {
    const minutes = numberAt(activity, 'duration_minutes');
    if (minutes === null || minutes <= 0) return '';
    if (minutes < 60) return `建议停留约 ${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    const remaining = minutes % 60;
    return `建议停留约 ${hours} 小时${remaining ? `${remaining} 分钟` : ''}`;
  };

  return (
    <section data-plan-section="itinerary" aria-labelledby="travel-plan-itinerary-heading">
      <h3 id="travel-plan-itinerary-heading">日程规划</h3>
      {days.length === 0 ? (
        <p>{sectionStatusText(section)}</p>
      ) : (
        <ol>
          {days.map((day, dayIndex) => {
            const dayNumber = numberAt(day, 'day') || dayIndex + 1;
            const date = textAt(day, 'date');
            const theme = textAt(day, 'theme');
            const activities = listAt(day, 'activities').filter(isRecord);
            return (
              <li key={`day-${dayNumber}`}>
                <h4>Day {dayNumber}{date ? ` · ${date}` : ' · 日期待定'}{theme ? ` · ${theme}` : ''}</h4>
                {activities.length === 0 ? (
                  <p>当天暂无已安排活动。</p>
                ) : (
                  <ol>
                    {activities.map((activity, activityIndex) => {
                      const startAt = textAt(activity, 'start_at');
                      const endAt = textAt(activity, 'end_at');
                      const place = recordAt(activity, 'place');
                      const placeName = textAt(place, 'name');
                      const time = startAt && endAt ? `建议 ${startAt}-${endAt}` : startAt ? `建议 ${startAt}` : '';
                      const description = textAt(place, 'summary') || notesById.get(textAt(activity, 'note_id')) || '';
                      const duration = durationText(activity);
                      const category = placeCategoryLabel(textAt(place, 'category'));
                      const mealLabels: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' };
                      const mealType = mealLabels[textAt(activity, 'meal_type')] || '';
                      const address = textAt(place, 'address');
                      const openingHours = textAt(place, 'opening_hours');
                      const route = recordAt(activity, 'route_to_next');
                      const routeMode = textAt(route, 'mode');
                      const routeMinutes = numberAt(route, 'duration_minutes');
                      const routeLabel = routeMode
                        ? `${routeModeLabel(routeMode)}${routeMinutes !== null ? ` · 约 ${routeMinutes} 分钟` : ''}`
                        : '待路线核验';
                      const activityRefs = referenceIds(
                        listAt(activity, 'evidence_refs'),
                        listAt(place, 'evidence_refs'),
                        textAt(recordAt(activity, 'reservation'), 'source_ref'),
                      );
                      const activityImages = displayableImageAssets(listAt(activity, 'images'), 3);
                      return (
                        <li className="travel-plan-activity" key={textAt(activity, 'activity_id') || `activity-${activityIndex}`}>
                          <div className="travel-plan-activity-heading">
                            <strong>{textAt(activity, 'title') || placeName || '活动待确认'}</strong>
                            <CitationMarkers sourceRefs={activityRefs} context={citations} />
                          </div>
                          {(time || duration || category || mealType) && (
                            <p className="travel-plan-activity-meta">
                              {[time, duration, category, mealType].filter(Boolean).join(' · ')}
                            </p>
                          )}
                          <ul className="travel-plan-activity-facts" aria-label={`${textAt(activity, 'title') || placeName || '活动'}的建议详情`}>
                            <li><span>营业时间</span><strong>{openingHours || '待地图或官方核验'}</strong></li>
                            <li><span>游览时间</span><strong>{duration || '待确认'}</strong></li>
                            <li><span>交通安排</span><strong>{routeLabel}</strong></li>
                            {description && <li><span>特色说明</span><strong>{description}</strong></li>}
                          </ul>
                          {address && address !== description && <p className="travel-plan-activity-address">地址：{address}</p>}
                          {activityImages.length > 0 && <TravelImageWithFallback images={activityImages} className="travel-plan-image--activity" imageLabel={`${textAt(activity, 'title') || placeName || '活动'}参考图片`} />}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
};

const renderBudgetAndReminders = (section: TravelPlanRecord, citations: CitationContext) => {
  const budget = recordAt(section, 'budget');
  const actionItems = listAt(section, 'action_items').filter(isRecord);
  const notes = listAt(section, 'notes').filter(isRecord);
  const estimatedTotal = isRecord(budget.estimated_total) ? formatMoney(budget.estimated_total) : '';
  const totalBudget = isRecord(budget.total_budget) ? formatMoney(budget.total_budget) : '';
  const unknownCostCount = numberAt(budget, 'unknown_cost_count');
  const categories = listAt(budget, 'categories').filter(isRecord);
  const travelerCosts = listAt(budget, 'traveler_costs').filter(isRecord);
  const categoryLabels: Record<string, string> = {
    transport: '交通',
    lodging: '住宿',
    food: '餐饮',
    activities: '活动与门票',
    shopping: '购物',
    other: '其他',
  };
  const travelerLabels: Record<string, string> = { adult: '成人', child: '儿童', senior: '老人' };
  const travelerStatusLabels: Record<string, string> = {
    standard_price: '按成人标准价计算',
    official_discount_verified: '已按官方优惠价计算',
    adult_price_assumed: '优惠待确认，暂按成人价计算',
    not_applicable: '暂无可按人数拆分的费用',
  };
  const warnings = listAt(budget, 'warnings').map((warning) => asText(warning)).filter(Boolean);

  return (
    <section data-plan-section="budget_and_reminders" aria-labelledby="travel-plan-budget-heading">
      <h3 id="travel-plan-budget-heading">预算与提醒</h3>
      {(totalBudget || estimatedTotal || unknownCostCount !== null) && (
        <dl>
          {totalBudget && <><dt>总预算</dt><dd>{totalBudget}</dd></>}
          {estimatedTotal && <><dt>预计支出</dt><dd>{estimatedTotal}</dd></>}
          {unknownCostCount !== null && <><dt>待确认费用</dt><dd>{unknownCostCount} 项</dd></>}
        </dl>
      )}
      {categories.length > 0 && (
        <>
          <h4>分类预算</h4>
          <ul>
            {categories.map((category, index) => (
              <li key={`${textAt(category, 'category') || 'other'}-${index}`}>
                {categoryLabels[textAt(category, 'category')] || '其他'}：{formatMoney(category.amount)}
              </li>
            ))}
          </ul>
        </>
      )}
      {travelerCosts.length > 0 && (
        <>
          <h4>按出行人估算</h4>
          <ul>
            {travelerCosts.map((traveler, index) => {
              const type = textAt(traveler, 'traveler_type');
              const count = numberAt(traveler, 'count');
              const status = textAt(traveler, 'pricing_status');
              return (
                <li key={`${type || 'traveler'}-${index}`}>
                  {travelerLabels[type] || '出行人'} {count ?? 0} 人：{formatMoney(traveler.estimated_total)}（{travelerStatusLabels[status] || '费用待确认'}）
                </li>
              );
            })}
          </ul>
        </>
      )}
      {actionItems.length > 0 && (
        <>
          <h4>出行提醒</h4>
          <ul>
            {actionItems.map((item, index) => (
              <li key={textAt(item, 'action_id') || `action-${index}`}>
                {textAt(item, 'text') || '提醒待确认'}
                <CitationMarkers sourceRefs={referenceIds(textAt(item, 'source_ref'))} context={citations} />
              </li>
            ))}
          </ul>
        </>
      )}
      {notes.length > 0 && (
        <>
          <h4>备注</h4>
          <ul>
            {notes.map((note, index) => (
              <li key={textAt(note, 'note_id') || `note-${index}`}>{textAt(note, 'content') || '备注待确认'}</li>
            ))}
          </ul>
        </>
      )}
      {warnings.length > 0 && <p role="note">注意：{warnings.join('；')}</p>}
    </section>
  );
};

const renderMapGuidance = (section: TravelPlanRecord) => {
  const locationIds = listAt(section, 'formal_location_ids');
  const routes = listAt(section, 'day_routes').filter(isRecord);
  const readyRoutes = routes.filter((route) => textAt(route, 'status') === 'ready').length;

  return (
    <section data-plan-section="map_guidance" aria-labelledby="travel-plan-map-heading">
      <h3 id="travel-plan-map-heading">地图提示</h3>
      <p>已同步 {locationIds.length} 个日程地点至旅行工作台。</p>
      {routes.length > 0 && <p>已核验 {readyRoutes}/{routes.length} 天路线。</p>}
      {textAt(section, 'status_reason') && <p>{textAt(section, 'status_reason')}</p>}
    </section>
  );
};

const browserDownload = (filename: string, blob: Blob) => {
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  window.document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};

const responseFilename = (header: string | null, fallback: string) => {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  return header?.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
};

const renderDelivery = (
  section: TravelPlanRecord,
  onDownload?: (format: DeliveryFormat) => void,
  downloading?: DeliveryFormat | null,
  status?: string,
) => {
  const files = [
    textAt(section, 'markdown_enabled') === 'true' ? textAt(section, 'markdown_filename') : '',
    textAt(section, 'pdf_enabled') === 'true' ? textAt(section, 'pdf_filename') : '',
  ].filter(Boolean);
  const markdownEnabled = isRecord(section) && section.markdown_enabled === true;
  const pdfEnabled = isRecord(section) && section.pdf_enabled === true;
  const enabledFiles = [
    markdownEnabled ? textAt(section, 'markdown_filename') : '',
    pdfEnabled ? textAt(section, 'pdf_filename') : '',
  ].filter(Boolean);

  return (
    <section data-plan-section="delivery" aria-labelledby="travel-plan-delivery-heading">
      <h3 id="travel-plan-delivery-heading">交付内容</h3>
      {enabledFiles.length > 0 ? (
        <div className="travel-plan-delivery-actions">
          {markdownEnabled && (
            <button type="button" disabled={!onDownload || Boolean(downloading)} onClick={() => onDownload?.('markdown')}>
              {downloading === 'markdown' ? '正在生成 Markdown…' : '下载 Markdown'}
              <span>{textAt(section, 'markdown_filename')}</span>
            </button>
          )}
          {pdfEnabled && (
            <button type="button" disabled={!onDownload || Boolean(downloading)} onClick={() => onDownload?.('pdf')}>
              {downloading === 'pdf' ? '正在生成 PDF…' : '下载 PDF'}
              <span>{textAt(section, 'pdf_filename')}</span>
            </button>
          )}
        </div>
      ) : files.length > 0 ? (
        <p>{files.join('、')}</p>
      ) : (
        <p>文件交付仍在准备。</p>
      )}
      {status && <p className="travel-plan-delivery-status" role="status">{status}</p>}
    </section>
  );
};

const renderSources = (
  sourceEntries: CitationEntry[],
  sourceListId: string,
  isOpen: boolean,
  onToggle: (open: boolean) => void,
) => {

  return (
    <section data-plan-section="sources" aria-labelledby="travel-plan-sources-heading">
      <h3 id="travel-plan-sources-heading">来源</h3>
      <details id={sourceListId} open={isOpen} onToggle={(event) => onToggle(event.currentTarget.open)}>
        <summary>查看参考来源（{sourceEntries.length}）</summary>
        {sourceEntries.length === 0 ? (
          <p>暂无可公开展示的来源。</p>
        ) : (
          <ol className="travel-plan-source-list">
            {sourceEntries.map((entry) => {
              const { source } = entry;
              const title = textAt(source, 'title') || textAt(source, 'source_name') || '未命名来源';
              const sourceName = textAt(source, 'source_name');
              const updatedAt = textAt(source, 'updated_at');
              const url = textAt(source, 'url');
              return (
                <li
                  key={entry.sourceId}
                  id={entry.targetId}
                  tabIndex={-1}
                  aria-label={`来源 ${entry.number}：${title}`}
                >
                  <span className="travel-plan-source-number" aria-hidden="true">{entry.number}.</span>
                  <span className="travel-plan-source-copy">
                    {url ? <a href={url} target="_blank" rel="noreferrer">{title}</a> : <strong>{title}</strong>}
                    <span className="travel-plan-source-meta">
                      {sourceName ? ` · ${sourceName}` : ''}
                      {updatedAt ? ` · 更新：${updatedAt}` : ''}
                    </span>
                  </span>
                  <span className="travel-plan-source-status">{sourceStatusLabel(source.status)}</span>
                </li>
              );
            })}
          </ol>
        )}
      </details>
    </section>
  );
};

const sectionRenderer: Record<
  Exclude<TravelPlanSectionName, 'sources'>,
  (section: TravelPlanRecord, isFlexible: boolean, citations: CitationContext) => JSX.Element
> = {
  destination_overview: (section, _isFlexible, citations) => renderDestinationOverview(section, citations),
  outbound_transport: renderOutboundTransport,
  lodging_plan: (section, _isFlexible, citations) => renderLodgingPlan(section, citations),
  itinerary: (section, _isFlexible, citations) => renderItinerary(section, citations),
  budget_and_reminders: (section, _isFlexible, citations) => renderBudgetAndReminders(section, citations),
  map_guidance: (section) => renderMapGuidance(section),
  delivery: (section) => renderDelivery(section),
};

export const TravelPlanMessage = ({
  tripId,
  planId,
  revision,
  sections = {},
  completed,
  document = null,
}: TravelPlanMessageProps) => {
  const resolvedSections = document ? documentSections(document) : sections;
  const overview = recordAt(resolvedSections.destination_overview, 'destination_overview');
  const intent = recordAt(resolvedSections.destination_overview, 'intent');
  const isFlexible = textAt(intent, 'date_mode') === 'flexible';
  const destination = textAt(overview, 'name_zh') || textAt(intent, 'destination');
  const isComplete = completed || Boolean(document);
  const sourceListId = useId();
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [sourceFocusTargetId, setSourceFocusTargetId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<DeliveryFormat | null>(null);
  const [downloadStatus, setDownloadStatus] = useState('');
  const sourceEntries = useMemo(() => {
    const sourceRecords = document
      ? document.sources.map((source) => source as unknown as TravelPlanRecord)
      : listAt(resolvedSections.sources, 'sources').filter(isRecord);
    const usedSourceIds = new Set<string>();

    return sourceRecords.flatMap((source) => {
      const sourceId = textAt(source, 'source_id');
      if (!sourceId || usedSourceIds.has(sourceId)) return [];
      usedSourceIds.add(sourceId);
      const number = usedSourceIds.size;
      return [{
        sourceId,
        number,
        source,
        targetId: `${sourceListId}-source-${number}`,
      }];
    });
  }, [document, resolvedSections.sources, sourceListId]);
  const citationContext = useMemo<CitationContext>(() => ({
    entriesBySourceId: new Map(sourceEntries.map((entry) => [entry.sourceId, entry])),
    onCitationClick: (sourceId) => {
      const entry = sourceEntries.find((candidate) => candidate.sourceId === sourceId);
      if (!entry) return;
      setSourceFocusTargetId(entry.targetId);
      setSourcesOpen(true);
    },
  }), [sourceEntries]);

  useEffect(() => {
    const targetId = sourceFocusTargetId;
    if (!sourcesOpen || !targetId) return;
    const target = window.document.getElementById(targetId);
    if (!target) return;
    target.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    target.focus({ preventScroll: true });
    setSourceFocusTargetId(null);
  }, [sourceFocusTargetId, sourcesOpen]);

  const downloadFormalDocument = async (format: DeliveryFormat) => {
    if (!document || downloading) return;
    setDownloading(format);
    setDownloadStatus('');
    try {
      const response = await fetch(`/api/trips/export/${format}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, expected_revision: document.revision }),
      });
      if (!response.ok) throw new Error(`下载失败（${response.status}）`);
      if (format === 'markdown') {
        const payload = await response.json() as { filename?: string; content?: string };
        browserDownload(
          payload.filename || document.delivery.markdown_filename || 'trip-plan.md',
          new Blob([payload.content || ''], { type: 'text/markdown;charset=utf-8' }),
        );
      } else {
        const blob = await response.blob();
        browserDownload(
          responseFilename(response.headers.get('content-disposition'), document.delivery.pdf_filename || 'trip-plan.pdf'),
          blob,
        );
      }
      setDownloadStatus(format === 'pdf' ? 'PDF 已开始下载' : 'Markdown 已开始下载');
    } catch (error) {
      setDownloadStatus(error instanceof Error ? error.message : '下载失败，请稍后重试');
    } finally {
      setDownloading(null);
    }
  };

  return (
    <article className="travel-plan-message" data-plan-id={planId} data-plan-revision={revision}>
      <header>
        <p>正式行程方案{destination ? ` · ${destination}` : ''}</p>
        <h2>方案版本 {revision}</h2>
      </header>
      {travelPlanSectionOrder.map((name) => {
        const section = resolvedSections[name];
        if (!isRecord(section)) return null;
        if (name === 'sources') {
          return (
            <Fragment key={name}>
              {renderSources(sourceEntries, sourceListId, sourcesOpen, setSourcesOpen)}
            </Fragment>
          );
        }
        if (name === 'delivery') {
          return (
            <Fragment key={name}>
              {renderDelivery(
                section,
                document ? (format) => void downloadFormalDocument(format) : undefined,
                downloading,
                downloadStatus,
              )}
            </Fragment>
          );
        }
        return <Fragment key={name}>{sectionRenderer[name](section, isFlexible, citationContext)}</Fragment>;
      })}
      {!isComplete && <p className="travel-plan-message-progress" role="status" aria-live="polite">正在整理后续章节</p>}
    </article>
  );
};

export default TravelPlanMessage;
