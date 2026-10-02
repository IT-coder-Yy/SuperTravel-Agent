import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TripProductTools, { buildChecklistItem, buildEffectiveTripPlan, buildTripNote } from './TripProductTools';

describe('TripProductTools', () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:test-download') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    Object.defineProperty(HTMLAnchorElement.prototype, 'click', { configurable: true, value: vi.fn() });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ templates: [{
        id: 'classic', name: '经典三日游', audience: ['首次到访'], budget_level: '中等', pace: 'balanced',
        default_days: 3, preferences: ['经典景点'], prompt: '使用模板并继续逐项澄清',
      }], content: '# 导出方案', filename: 'trip-plan.md' }),
      blob: async () => new Blob(['pdf'], { type: 'application/pdf' }),
      headers: { get: () => null },
    }));
  });

  it('uses a template as intent and continues clarification', async () => {
    const onUseTemplate = vi.fn();
    render(<TripProductTools plan={null} workspace={{ days: [], locations: [], sources: [], budget: null, validation: null, repair: null }} onUseTemplate={onUseTemplate} />);
    fireEvent.click(screen.getByRole('button', { name: /行程工具/ }));
    await waitFor(() => expect(screen.getByText('经典三日游')).not.toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '使用并继续澄清' }));
    expect(onUseTemplate).toHaveBeenCalledWith('使用模板并继续逐项澄清');
  });

  it('binds checklist items and notes to concrete activities', () => {
    const workspace = {
      days: [{
        id: 'day-1', day: 1, date: null, theme: '西湖', revision: 0, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [{
          id: 'activity-west-lake', day: 1, start_time: '09:00', end_time: '11:00',
          title: '游览西湖', place: { name: '西湖' }, estimated_cost: 0, notes: [],
          data_type: null, source: null, sources: [], evidence_refs: [], route_to_next: null,
        }],
      }],
      locations: [{ id: 'poi-west-lake', name: '西湖', lat: 30.24, lng: 120.15 }],
      sources: [], budget: null, validation: null, repair: null,
    };
    const item = buildChecklistItem('item-1', '购买门票', 'activity:activity-west-lake', workspace);
    const note = buildTripNote('note-1', '下午拍照', 'activity', 'activity-west-lake', workspace);
    expect(item).toMatchObject({ activity_id: 'activity-west-lake', day: 1 });
    expect(note).toMatchObject({ target_type: 'activity', target_id: 'activity-west-lake', activity_id: 'activity-west-lake', day: 1 });
  });

  it('uses a restored workspace when activeTripPlan is absent', () => {
    const workspace = {
      days: [{ id: 'day-1', day: 1, date: null, theme: null, revision: 1, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [] }],
      locations: [], sources: [], budget: null, validation: null, repair: null,
    };
    expect(buildEffectiveTripPlan(null, workspace)).toMatchObject({
      plan_id: 'workspace-restored-draft', version: 1, days: 1,
    });
  });

  it('keeps the editable document aligned with the active plan version', async () => {
    const onDocumentChange = vi.fn();
    const workspace = {
      days: [{ id: 'day-1', day: 1, date: null, theme: '新版', revision: 2, estimated_cost: null,
        warnings: [], data_type: null, source: null, sources: [], activities: [{
          id: 'new-activity', day: 1, start_time: null, end_time: null, title: '新版景点',
          place: { poi_id: 'new-poi', name: '新版景点', lat: 30.1, lng: 120.1 }, estimated_cost: 0,
          notes: [], data_type: null, source: null, sources: [], evidence_refs: [], route_to_next: null,
        }] }],
      locations: [{ id: 'new-poi', name: '新版景点', lat: 30.1, lng: 120.1 }],
      sources: [], budget: null, validation: null, repair: null,
    };
    const plan = {
      plan_id: 'plan-1', version: 2, title: '杭州新版行程', intent: { destination: '杭州', days: 1 }, days: 1,
      trip_days: workspace.days.map((day) => ({ ...day, activities: day.activities.map((activity) => ({ ...activity, activity_id: activity.id })) })),
    };
    const document = {
      schema_version: '2.0', plan_id: 'plan-1', version: 1, title: '旧版', intent: {},
      itinerary: { status: 'ready', days: [] }, map_guidance: { location_ids: [] }, delivery: {}, checklist: [], notes: [],
    };

    render(<TripProductTools plan={plan} document={document} workspace={workspace} onDocumentChange={onDocumentChange} />);

    await waitFor(() => expect(onDocumentChange).toHaveBeenCalled());
    const emitted = onDocumentChange.mock.calls.at(-1)?.[0];
    expect(emitted).toMatchObject({ version: 2, title: '杭州新版行程' });
    expect(emitted.itinerary.days[0].activities[0].title).toBe('新版景点');
    expect(emitted.map_guidance.location_ids).toEqual(['new-poi']);
    expect(emitted.delivery.markdown_filename).toBe('杭州新版行程-v2.md');
  });

  it('does not overwrite a V3 draft with legacy workspace fields', async () => {
    const onDocumentChange = vi.fn();
    const emptyWorkspace = { days: [], locations: [], sources: [], budget: null, validation: null, repair: null };
    const draft = { schema_version: '3.0', plan_id: 'v3', revision: 2, status: 'draft',
      itinerary: { days: [] }, budget: { categories: [] }, notes: [], checklist: [] };
    const { rerender } = render(<TripProductTools
      plan={{ plan_id: 'v3', version: 2, trip_days: [{ day: 1, activities: [] }] }}
      document={draft} workspace={emptyWorkspace} onDocumentChange={onDocumentChange}
    />);
    await act(async () => {});
    expect(onDocumentChange).not.toHaveBeenCalled();
    rerender(<TripProductTools plan={{ plan_id: 'v3', version: 3 }}
      document={{ ...draft, revision: 3 }} workspace={emptyWorkspace} onDocumentChange={onDocumentChange} />);
    await act(async () => {});
    expect(onDocumentChange).not.toHaveBeenCalled();
    expect(draft.itinerary).toEqual({ days: [] });
  });

  it('offers only the saved formal version when an unapplied draft cannot be committed', async () => {
    const fetchMock = vi.mocked(fetch);
    const formalDocument = {
      schema_version: '3.0', plan_id: 'plan-formal', revision: 2, status: 'formal',
      title: '已保存方案', delivery: { markdown_filename: 'saved.md', pdf_filename: 'saved.pdf' },
    };
    render(<TripProductTools
      tripId="export-trip"
      plan={{ plan_id: 'plan-formal', version: 3 }}
      document={{ ...formalDocument, revision: 3, title: '未应用草稿' }}
      formalDocument={formalDocument}
      hasDraft
      canApplyDraft={false}
      workspace={{ days: [], locations: [], sources: [], budget: null, validation: null, repair: null }}
    />);

    fireEvent.click(screen.getByRole('button', { name: /行程工具/ }));
    fireEvent.click(screen.getByRole('tab', { name: '下载' }));
    fireEvent.click(screen.getByRole('button', { name: /导出 Markdown/ }));

    expect(screen.getByText('检测到未应用修改')).not.toBeNull();
    expect(screen.getByRole('button', { name: '先应用再下载' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '下载已保存版本' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/trips/export/markdown',
      expect.objectContaining({ body: JSON.stringify({ trip_id: "export-trip", expected_revision: formalDocument.revision }) }),
    ));
  });

  it('applies a draft before downloading the resulting formal version', async () => {
    const fetchMock = vi.mocked(fetch);
    const appliedDocument = {
      schema_version: '3.0', plan_id: 'plan-formal', revision: 3, status: 'formal',
      title: '已应用方案', delivery: { markdown_filename: 'applied.md', pdf_filename: 'applied.pdf' },
    };
    const onApplyDraft = vi.fn().mockResolvedValue(appliedDocument);
    render(<TripProductTools
      tripId="export-trip"
      plan={{ plan_id: 'plan-formal', version: 3 }}
      document={{ ...appliedDocument, revision: 3 }}
      formalDocument={{ ...appliedDocument, revision: 2 }}
      hasDraft
      canApplyDraft
      onApplyDraft={onApplyDraft}
      workspace={{ days: [], locations: [], sources: [], budget: null, validation: null, repair: null }}
    />);

    fireEvent.click(screen.getByRole('button', { name: /行程工具/ }));
    fireEvent.click(screen.getByRole('tab', { name: '下载' }));
    fireEvent.click(screen.getByRole('button', { name: /导出 PDF/ }));
    fireEvent.click(screen.getByRole('button', { name: '先应用再下载' }));

    await waitFor(() => expect(onApplyDraft).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/trips/export/pdf',
      expect.objectContaining({ body: JSON.stringify({ trip_id: "export-trip", expected_revision: appliedDocument.revision }) }),
    ));
  });

  it('keeps only formal Markdown and PDF downloads in the main tool panel', () => {
    const formalDocument = {
      schema_version: '3.0', plan_id: 'plan-formal', revision: 2, status: 'formal',
      title: '已保存方案', delivery: { markdown_filename: 'saved.md', pdf_filename: 'saved.pdf' },
    };
    render(<TripProductTools
      tripId="export-trip"
      plan={{ plan_id: 'plan-formal', version: 2 }}
      document={formalDocument}
      formalDocument={formalDocument}
      workspace={{ days: [], locations: [], sources: [], budget: null, validation: null, repair: null }}
    />);

    fireEvent.click(screen.getByRole('button', { name: /下载/ }));
    expect(screen.getByRole('tab', { name: '下载' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByRole('tab', { name: '分享' })).toBeNull();

    expect(screen.getByRole('button', { name: /导出 Markdown/ })).not.toBeNull();
    expect(screen.getByRole('button', { name: /导出 PDF/ })).not.toBeNull();
    expect(screen.queryByText('导入 JSON/Markdown')).toBeNull();
    expect(screen.queryByText('导出 JSON')).toBeNull();
  });
});
