import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import formalFixture from '../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import { restoreFormalSnapshotState } from '../features/travel/state/formalSnapshotRestore';
import { TravelPlannerStateProvider, useTravelPlannerState } from '../features/travel/state/TravelPlannerStateProvider';
import ChatInterface, { type ChatInterfaceRef } from './ChatInterface';
import { createTripEditDraft, readTripEditDraftMirror, writeTripEditDraftMirror } from '../features/travel/state/tripEditDraft';

vi.mock('../hooks/useChatHistory', () => ({ useChatHistory: () => ({ saveChat: vi.fn(), getChat: vi.fn() }) }));
vi.mock('./MapComponent', () => ({ default: () => <div data-testid="map" /> }));
vi.mock('../features/travel/create/TripCreateForm', () => ({ default: () => null }));
vi.mock('./TripProductTools', () => ({ default: () => null }));

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ servers: [], skills: [], cases: [] }) })));
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn();
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const StateProbe = () => {
  const { state, dispatch } = useTravelPlannerState();
  return <><button onClick={() => dispatch({ type: 'field/set', field: 'selectedLocationId', value: state.mapLocations[0]?.id || '' })}>选择活动</button>
    <button onClick={() => dispatch({ type: 'field/set', field: 'selectedLocationId', value: formalFixture.candidate_pool[0].activity_id })}>选择候选</button>
    <output data-testid="selection">{state.selectedLocationId}</output>
    <output data-testid="revision">{String(state.activeTripDocument?.revision)}</output></>;
};

it('同旅程迟到详情保留日程视图，同时接受新修订和服务端已清空草稿', async () => {
  const viewFor = (revision: number, chatId = 'same-trip') => {
    const restored = restoreFormalSnapshotState({ current: { document: { ...formalFixture, revision } } })!;
    return <TravelPlannerStateProvider>
      <ChatInterface currentChatId={chatId} loadedMessages={[]}
        loadedTripDocument={restored.document} loadedTripPlan={restored.plan}
        loadedTripWorkspace={restored.workspace as unknown as Record<string, unknown>}
        loadedTripDraft={null} />
      <StateProbe />
    </TravelPlannerStateProvider>;
  };
  const view = render(viewFor(1));
  fireEvent.click(await screen.findByRole('button', { name: /编辑行程/ }));
  await waitFor(() => expect(screen.getByRole('tab', { name: '日程' })).toBeTruthy());
  fireEvent.click(screen.getByRole('tab', { name: '日程' }));
  fireEvent.click(screen.getByText('选择活动'));
  const selectedId = screen.getByTestId('selection').textContent;
  expect(selectedId).toBeTruthy();
  expect(screen.getByRole('tab', { name: '日程' }).getAttribute('aria-selected')).toBe('true');
  // 响应是同一正式版本的新对象，也不能让用户回到概览。
  view.rerender(viewFor(1));
  await waitFor(() => expect(screen.getByRole('tab', { name: '日程' }).getAttribute('aria-selected')).toBe('true'));
  expect(screen.getByTestId('selection').textContent).toBe(selectedId);
  writeTripEditDraftMirror('same-trip', createTripEditDraft({ ...formalFixture, revision: 12 }, []));
  view.rerender(viewFor(2));
  await waitFor(() => {
    expect(screen.getByRole('tab', { name: '日程' }).getAttribute('aria-selected')).toBe('true');
    expect(readTripEditDraftMirror('same-trip')).toBeNull();
    expect(screen.getByTestId('revision').textContent).toBe('2');
    expect(screen.getByTestId('selection').textContent).toBe(selectedId);
  });
  view.rerender(viewFor(2, 'other-trip'));
  await waitFor(() => expect(screen.getByTestId('selection').textContent).toBe(''));
});

it.each(['活动', '候选'])('同旅程保留有效%s选择，新修订移除该地点后清空', async (kind) => {
  const selectedId = kind === '候选' ? formalFixture.candidate_pool[0].activity_id : formalFixture.itinerary.days[0].activities[0].activity_id;
  const expectedLocationId = kind === '候选' ? selectedId : formalFixture.itinerary.days[0].activities[0].place.poi_id;
  const viewFor = (revision: number, removed = false) => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    document.revision = revision;
    if (removed) {
      document.candidate_pool = document.candidate_pool.filter((item: any) => item.activity_id !== selectedId);
      document.itinerary.days.forEach((day: any) => { day.activities = day.activities.filter((item: any) => item.activity_id !== selectedId); });
    }
    const restored = restoreFormalSnapshotState({ current: { document } })!;
    return <TravelPlannerStateProvider>
      <ChatInterface currentChatId="same-trip" loadedMessages={[]}
        loadedTripDocument={restored.document} loadedTripPlan={restored.plan}
        loadedTripWorkspace={restored.workspace as unknown as Record<string, unknown>} loadedTripDraft={null} />
      <StateProbe />
    </TravelPlannerStateProvider>;
  };
  const view = render(viewFor(1));
  await waitFor(() => expect(screen.getByTestId('revision').textContent).toBe('1'));
  fireEvent.click(screen.getByText(`选择${kind}`));
  expect(screen.getByTestId('selection').textContent).toBe(expectedLocationId);
  view.rerender(viewFor(2));
  await waitFor(() => expect(screen.getByTestId('revision').textContent).toBe('2'));
  expect(screen.getByTestId('selection').textContent).toBe(expectedLocationId);
  view.rerender(viewFor(3, true));
  await waitFor(() => expect(screen.getByTestId('selection').textContent).toBe(''));
});

it.each(['新会话实时结果', '命令式加载'])('经过%s后选回历史，不继承另一个会话的同地点选择', async (entry) => {
  const ref = createRef<ChatInterfaceRef>();
  const previous = restoreFormalSnapshotState({ current: { document: formalFixture } })!;
  const other = restoreFormalSnapshotState({ current: { document: { ...formalFixture, plan_id: 'different-plan', revision: 2 } } })!;
  // 模拟实时正式结果写入共享状态；此路径不会触发 loadedMessages 历史恢复。
  const RuntimeResult = () => {
    const { dispatch } = useTravelPlannerState();
    return <button onClick={() => dispatch({ type: 'trip/restore', plan: other.plan, document: other.document,
      workspace: other.workspace, locations: other.workspace.locations })}>接收新正式结果</button>;
  };
  const viewFor = (chatId: string) => <TravelPlannerStateProvider>
    <ChatInterface ref={ref} currentChatId={chatId} loadedMessages={chatId === 'history-A' ? [] : null}
      loadedTripDocument={chatId === 'history-A' ? previous.document : null}
      loadedTripPlan={chatId === 'history-A' ? previous.plan : null}
      loadedTripWorkspace={chatId === 'history-A' ? previous.workspace as unknown as Record<string, unknown> : null}
      loadedTripDraft={null} />
    <StateProbe /><RuntimeResult />
  </TravelPlannerStateProvider>;
  const view = render(viewFor('history-A'));
  await waitFor(() => expect(screen.getByTestId('revision').textContent).toBe('1'));
  if (entry === '新会话实时结果') {
    act(() => { ref.current!.startNewChat(); });
    view.rerender(viewFor(''));
    fireEvent.click(screen.getByText('接收新正式结果'));
  } else {
    view.rerender(viewFor('session-B'));
    act(() => { ref.current!.loadChat([], other.plan, other.workspace as unknown as Record<string, unknown>, other.document); });
  }
  await waitFor(() => expect(screen.getByTestId('revision').textContent).toBe('2'));
  fireEvent.click(screen.getByText('选择活动'));
  expect(screen.getByTestId('selection').textContent).toBe(formalFixture.itinerary.days[0].activities[0].place.poi_id);
  view.rerender(viewFor('history-A'));
  await waitFor(() => {
    expect(screen.getByTestId('revision').textContent).toBe('1');
    expect(screen.getByTestId('selection').textContent).toBe('');
  });
});
