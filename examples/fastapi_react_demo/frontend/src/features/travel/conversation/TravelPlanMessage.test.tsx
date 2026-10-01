import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TravelPlanMessage, {
  type TravelPlanSectionMap,
  travelPlanSectionOrder,
} from './TravelPlanMessage';

const sections: Partial<TravelPlanSectionMap> = {
  itinerary: {
    days: [{
      day: 1,
      date: null,
      theme: '慢游老城',
      activities: [{
        activity_id: 'activity-1',
        title: '夫子庙',
        start_at: '09:30',
        end_at: '11:00',
        place: { name: '夫子庙景区' },
      }],
    }],
  },
  destination_overview: {
    title: '南京三日游',
    intent: {
      origin: '上海',
      destination: '南京',
      date_mode: 'flexible',
      days: 3,
    },
    destination_overview: {
      name_zh: '南京',
      summary: '以城市漫游与历史文化为主。',
      themes: ['人文', '美食'],
    },
  },
  outbound_transport: {
    outbound_transport: {
      options: [{
        option_id: 'outbound-1',
        mode: 'train',
        service_number: 'G123',
        departure_place: '上海虹桥站',
        arrival_place: '南京南站',
        departure_time: { display_text: '08:00' },
        arrival_time: { display_text: '09:30' },
      }],
    },
    return_transport: {
      options: [{
        option_id: 'return-1',
        mode: 'train',
        service_number: 'G456',
        departure_place: '南京南站',
        arrival_place: '上海虹桥站',
      }],
    },
  },
  lodging_plan: {
    planning_lodging_id: 'hotel-1',
    options: [{
      lodging_id: 'hotel-1',
      name: '老门东附近住宿',
      area: '秦淮区',
      nightly_price: { amount: 560, currency: 'CNY' },
      reasons: ['步行可达多个日程地点'],
    }],
  },
  budget_and_reminders: {
    budget: {
      total_budget: { amount: 3000, currency: 'CNY' },
      estimated_total: { amount: 2600, currency: 'CNY' },
      unknown_cost_count: 1,
    },
    action_items: [{ action_id: 'reminder-1', text: '出发前确认景区预约。' }],
  },
  map_guidance: {
    formal_location_ids: ['activity-1'],
    day_routes: [{ day: 1, status: 'ready' }],
  },
  delivery: {
    markdown_enabled: true,
    markdown_filename: '南京三日游.md',
    pdf_enabled: false,
    pdf_filename: '南京三日游.pdf',
  },
  sources: {
    sources: [{
      source_id: 'source-1',
      title: '南京文旅官方信息',
      source_name: '南京文旅',
      url: 'https://example.com/nanjing',
    }],
  },
};

describe('TravelPlanMessage', () => {
  it('uses the fixed V3 section order and only shows received stream sections', () => {
    render(
      <TravelPlanMessage
        planId="plan-nanjing"
        revision={3}
        sections={sections}
        completed={false}
      />,
    );

    const renderedSections = Array.from(document.querySelectorAll<HTMLElement>('[data-plan-section]'))
      .map((element) => element.dataset.planSection);
    expect(renderedSections).toEqual(travelPlanSectionOrder);
    expect(screen.getByRole('status').textContent).toContain('正在整理后续章节');
    expect(screen.getByText('南京三日游.md')).toBeTruthy();
    expect(screen.getByText('老门东附近住宿')).toBeTruthy();
  });

  it('keeps date-flexible plans free from concrete transport service details', () => {
    render(
      <TravelPlanMessage
        planId="plan-nanjing"
        revision={3}
        sections={sections}
        completed={false}
      />,
    );

    expect(screen.getAllByText(/日期待定，暂不展示具体班次/)).toHaveLength(2);
    expect(screen.queryByText('G123')).toBeNull();
    expect(screen.queryByText('08:00')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Day 1 · 日期待定 · 慢游老城' })).toBeTruthy();
  });

  it('shows provider-returned seat classes and remaining tickets for fixed-date transport', () => {
    const fixedSections: Partial<TravelPlanSectionMap> = {
      ...sections,
      destination_overview: {
        ...sections.destination_overview!,
        intent: {
          origin: '上海',
          destination: '南京',
          date_mode: 'fixed',
          start_date: '2026-08-01',
          end_date: '2026-08-03',
          days: 3,
        },
      },
      outbound_transport: {
        outbound_transport: {
          selected_option_id: 'outbound-1',
          options: [{
            option_id: 'outbound-1',
            mode: 'train',
            service_number: 'G123',
            departure_place: '上海虹桥站',
            arrival_place: '南京南站',
            departure_time: { display_text: '08:00' },
            arrival_time: { display_text: '09:30' },
            seat_options: [
              { name: '一等座', remaining_text: '2', availability: 'limited', price: { amount: 251, currency: 'CNY' } },
              { name: '二等座', remaining_text: '18', availability: 'available', price: { amount: 157, currency: 'CNY' } },
            ],
          }],
        },
        return_transport: { options: [] },
      },
    };

    render(<TravelPlanMessage planId="plan-nanjing" revision={3} sections={fixedSections} completed />);

    expect(screen.getByText('一等座')).toBeTruthy();
    expect(screen.getByText(/规划采用，未购票/)).toBeTruthy();
    expect(screen.getByText('2 · ¥251')).toBeTruthy();
    expect(screen.getByText('二等座')).toBeTruthy();
    expect(screen.getByText('18 · ¥157')).toBeTruthy();
  });

  it('labels international transport with local and Beijing time in the formal plan', () => {
    const fixedSections: Partial<TravelPlanSectionMap> = {
      ...sections,
      destination_overview: {
        ...sections.destination_overview!,
        intent: { origin: '上海', destination: '东京', date_mode: 'fixed', days: 3 },
      },
      outbound_transport: {
        outbound_transport: {
          scope: 'international',
          options: [{
            option_id: 'flight-1',
            mode: 'flight',
            departure_place: '上海浦东机场',
            arrival_place: '东京成田机场',
            departure_time: {
              display_text: '09:00',
              utc: '2026-10-01T01:00:00Z',
              local_iso: '2026-10-01T09:00:00+08:00',
              timezone: 'Asia/Shanghai',
              beijing_iso: '2026-10-01T09:00:00+08:00',
            },
            arrival_time: {
              display_text: '01:00',
              utc: '2026-10-01T16:00:00Z',
              local_iso: '2026-10-02T01:00:00+09:00',
              timezone: 'Asia/Tokyo',
              beijing_iso: '2026-10-02T00:00:00+08:00',
              day_offset: 1,
            },
          }],
        },
        return_transport: { options: [] },
      },
    };

    const { container } = render(<TravelPlanMessage planId="international-time" revision={1} sections={fixedSections} completed />);

    const expectedTimes = '出发：当地时间 2026-10-01 09:00（Asia/Shanghai） / 北京时间 2026-10-01 09:00；到达：当地时间 2026-10-02 01:00（Asia/Tokyo） / 北京时间 2026-10-02 00:00（次日抵达）';
    expect(Array.from(container.querySelectorAll('div')).some((element) => (
      element.textContent?.includes(expectedTimes)
    ))).toBe(true);
  });

  it('keeps no-result transport useful without inventing a service and links to the official query', () => {
    const fixedSections: Partial<TravelPlanSectionMap> = {
      ...sections,
      destination_overview: {
        ...sections.destination_overview!,
        intent: { origin: '上海', destination: '南京', date_mode: 'fixed', days: 3 },
      },
      outbound_transport: {
        outbound_transport: {
          options: [],
          status_reason: '暂无可靠实时票务数据。可先按“上海 → 南京”比较交通方式。',
          official_query_url: 'https://www.12306.cn/index/',
        },
        return_transport: { options: [], status_reason: '暂无可靠实时票务数据。' },
      },
    };

    render(<TravelPlanMessage planId="transport-fallback" revision={1} sections={fixedSections} completed />);

    expect(screen.getByText((_, element) => Boolean(
      element?.tagName === 'P'
      && element.textContent?.includes('暂无可靠实时票务数据。可先按“上海 → 南京”比较交通方式。'),
    ))).toBeTruthy();
    expect(screen.getByRole('link', { name: '前往官方查询' }).getAttribute('href')).toBe('https://www.12306.cn/index/');
  });

  it('renders all V3 sections from a completed document without the stream progress state', () => {
    const documentFixture = {
      plan_id: 'document-plan',
      revision: 4,
      title: '杭州三日游',
      intent: {
        origin: '上海',
        destination: '杭州',
        date_mode: 'fixed',
        start_date: '2026-08-01',
        end_date: '2026-08-03',
        days: 3,
      },
      destination_overview: {
        name_zh: '杭州',
        themes: [],
        cover_image: {
          image_id: 'hangzhou-cover',
          url: 'https://images.unsplash.com/photo-123',
          alt: '杭州西湖',
          display_allowed: true,
          export_allowed: false,
          attribution_required: true,
          attribution_text: '摄影师甲',
          attribution_url: 'https://unsplash.com/@photographer-a',
          provider_name: 'Unsplash',
          provider_url: 'https://unsplash.com/photos/example',
          source_ref: 'source-cover',
        },
      },
      outbound_transport: { options: [] },
      return_transport: { options: [] },
      lodging_plan: { options: [] },
      itinerary: { days: [] },
      budget: { total_budget: { amount: 5000, currency: 'CNY' }, unknown_cost_count: 0, warnings: [] },
      candidate_pool: [],
      action_items: [],
      notes: [],
      map_guidance: { formal_location_ids: [], day_routes: [] },
      delivery: { markdown_enabled: true, markdown_filename: '杭州三日游.md', pdf_enabled: true, pdf_filename: '杭州三日游.pdf' },
      sources: [],
    } as never;

    render(
      <TravelPlanMessage
        planId="document-plan"
        revision={4}
        document={documentFixture}
        completed={false}
      />,
    );

    expect(document.querySelectorAll('[data-plan-section]')).toHaveLength(8);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByText('杭州三日游.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: /下载 Markdown.*杭州三日游\.md/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /下载 PDF.*杭州三日游\.pdf/ })).toBeTruthy();
    expect(screen.getByRole('img', { name: '杭州西湖' })).toBeTruthy();
    expect(screen.queryByText('图片信息')).toBeNull();
  });

  it('renders suggested timing, duration and the place description inside a daily activity', () => {
    const documentFixture = {
      plan_id: 'detail-plan',
      revision: 1,
      title: '杭州三日游',
      intent: { origin: '上海', destination: '杭州', date_mode: 'fixed', days: 1 },
      destination_overview: { name_zh: '杭州', summary: '以湖山与城市漫游为主。', themes: [] },
      outbound_transport: { options: [] },
      return_transport: { options: [] },
      lodging_plan: { options: [] },
      itinerary: {
        days: [{
          day: 1,
          activities: [{
            activity_id: 'west-lake',
            title: '西湖漫步',
            start_at: '09:30',
            end_at: '11:30',
            duration_minutes: 120,
            note_id: 'west-lake-note',
            route_to_next: { mode: 'walk', duration_minutes: 16 },
            place: { name: '西湖景区', category: 'attraction', summary: '沿湖串联堤桥、园林与观景点，适合放慢节奏步行游览。', address: '杭州市西湖区龙井路 1 号', opening_hours: '全天开放' },
          }, {
            activity_id: 'west-lake-lunch',
            title: '西湖午餐',
            start_at: '12:00',
            end_at: '13:30',
            duration_minutes: 90,
            meal_type: 'lunch',
            place: { name: '西湖午餐', category: 'food', opening_hours: '11:00-14:00' },
          }],
        }],
      },
      budget: { total_budget: { amount: 5000, currency: 'CNY' }, unknown_cost_count: 0, warnings: [] },
      candidate_pool: [],
      action_items: [],
      notes: [{ note_id: 'west-lake-note', scope: 'activity', target_id: 'west-lake', content: '这里是活动备注。' }],
      map_guidance: { formal_location_ids: [], day_routes: [] },
      delivery: { markdown_enabled: true, markdown_filename: '杭州三日游.md', pdf_enabled: true, pdf_filename: '杭州三日游.pdf' },
      sources: [],
    } as never;

    render(<TravelPlanMessage planId="detail-plan" revision={1} document={documentFixture} completed />);

    expect(screen.getByText('建议 09:30-11:30 · 建议停留约 2 小时 · 景点')).toBeTruthy();
    expect(screen.getByText('建议 12:00-13:30 · 建议停留约 1 小时30 分钟 · 吃喝 · 午餐')).toBeTruthy();
    expect(screen.getAllByText('营业时间')).toHaveLength(2);
    expect(screen.getByText('全天开放')).toBeTruthy();
    expect(screen.getAllByText('游览时间')).toHaveLength(2);
    expect(screen.getAllByText('交通安排')).toHaveLength(2);
    expect(screen.getByText('步行 · 约 16 分钟')).toBeTruthy();
    expect(screen.getByText('特色说明')).toBeTruthy();
    expect(screen.getByText('沿湖串联堤桥、园林与观景点，适合放慢节奏步行游览。')).toBeTruthy();
    expect(screen.getByText('地址：杭州市西湖区龙井路 1 号')).toBeTruthy();
    expect(screen.queryByText('这里是活动备注。')).toBeNull();
  });

  it('reuses one source number across sections and opens the matching source from its citation', async () => {
    const documentFixture = {
      plan_id: 'citation-plan',
      revision: 1,
      title: '杭州三日游',
      intent: {
        origin: '上海',
        destination: '杭州',
        date_mode: 'fixed',
        start_date: '2026-08-01',
        end_date: '2026-08-03',
        days: 3,
      },
      destination_overview: {
        name_zh: '杭州',
        summary: '以西湖周边与城市美食为主。',
        themes: [],
        evidence_refs: ['source-guide'],
      },
      outbound_transport: {
        options: [{
          option_id: 'outbound-1',
          mode: 'train',
          departure_place: '上海虹桥站',
          arrival_place: '杭州东站',
          source_refs: ['source-official', 'source-guide'],
        }],
      },
      return_transport: { options: [] },
      lodging_plan: {
        options: [{
          lodging_id: 'lodging-1',
          name: '西湖附近住宿',
          source_refs: ['source-guide'],
        }],
      },
      itinerary: {
        days: [{
          day: 1,
          date: '2026-08-01',
          activities: [{
            activity_id: 'activity-1',
            title: '西湖漫步',
            evidence_refs: ['source-map'],
            place: { name: '西湖景区', evidence_refs: ['source-guide'] },
          }],
        }],
      },
      budget: { total_budget: { amount: 5000, currency: 'CNY' }, unknown_cost_count: 0, warnings: [] },
      candidate_pool: [],
      action_items: [{ action_id: 'reminder-1', text: '提前确认预约规则。', source_ref: 'source-official' }],
      notes: [],
      map_guidance: { formal_location_ids: [], day_routes: [] },
      delivery: { markdown_enabled: true, markdown_filename: '杭州三日游.md', pdf_enabled: true, pdf_filename: '杭州三日游.pdf' },
      sources: [
        {
          source_id: 'source-guide',
          title: '杭州文旅指南',
          source_name: '杭州文旅',
          status: 'guide_reference',
          url: 'https://example.com/guide',
        },
        {
          source_id: 'source-official',
          title: '铁路票务查询',
          source_name: '铁路票务平台',
          status: 'realtime_verified',
        },
        {
          source_id: 'source-map',
          title: '西湖地图地点',
          source_name: '地图地点核验',
          status: 'map_reference',
        },
      ],
    } as never;

    render(
      <TravelPlanMessage
        planId="citation-plan"
        revision={1}
        document={documentFixture}
        completed
      />,
    );

    const sourceOneCitations = screen.getAllByRole('button', { name: '查看参考来源 1' });
    expect(sourceOneCitations).toHaveLength(4);
    expect(screen.getAllByRole('button', { name: '查看参考来源 2' })).toHaveLength(2);
    const sourceDetails = screen.getByText('查看参考来源（3）').closest('details');
    expect(sourceDetails?.open).toBe(false);

    fireEvent.click(sourceOneCitations[0]);

    await waitFor(() => {
      const sourceItem = screen.getByRole('listitem', { name: '来源 1：杭州文旅指南' });
      expect(sourceDetails?.open).toBe(true);
      expect(document.activeElement).toBe(sourceItem);
    });
    expect(screen.getByText('攻略参考')).toBeTruthy();
  });

  it('renders traveler pricing policy and foreign-currency CNY reference in budget', () => {
    const budgetSections: Partial<TravelPlanSectionMap> = {
      ...sections,
      budget_and_reminders: {
        budget: {
          total_budget: { amount: 500000, currency: 'JPY', cny_reference_amount: 25000, exchange_rate_as_of: '2026-07-28' },
          estimated_total: { amount: 420000, currency: 'JPY', cny_reference_amount: 21000 },
          unknown_cost_count: 0,
          categories: [{ category: 'activities', amount: { amount: 15000, currency: 'JPY', cny_reference_amount: 750 } }],
          traveler_costs: [{ traveler_type: 'child', count: 1, estimated_total: { amount: 250, currency: 'CNY' }, pricing_status: 'official_discount_verified' }],
          warnings: [],
        },
        action_items: [],
      },
    };

    render(<TravelPlanMessage planId="plan-budget" revision={1} sections={budgetSections} completed />);

    expect(screen.getByText('JPY 500,000（约¥25,000，汇率参考日期 2026-07-28）')).toBeTruthy();
    expect(screen.getByText('活动与门票：JPY 15,000（约¥750）')).toBeTruthy();
    expect(screen.getByText('儿童 1 人：¥250（已按官方优惠价计算）')).toBeTruthy();
  });
});
