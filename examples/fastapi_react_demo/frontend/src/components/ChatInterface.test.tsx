import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import formalFixture from '../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import { restoreFormalSnapshotState } from '../features/travel/state/formalSnapshotRestore';
import { TravelPlannerStateProvider, useTravelPlannerState } from '../features/travel/state/TravelPlannerStateProvider';
import ChatInterface from './ChatInterface';
import { createTripEditDraft, readTripEditDraftMirror, writeTripEditDraftMirror } from '../features/travel/state/tripEditDraft';

vi.mock('../hooks/useChatHistory', () => ({
  useChatHistory: () => ({ saveChat: vi.fn(), getChat: vi.fn() }),
}));
vi.mock('../features/travel/workbench/TravelPlannerWorkbench', () => ({
  default: () => <div data-testid="restored-workbench" />,
  buildCandidateMapLocations: () => [],
}));
vi.mock('../features/travel/create/TripCreateForm', () => ({ default: () => <div data-testid="create-form" /> }));
vi.mock('./TripProductTools', () => ({ default: () => null }));

const StateProbe = () => {
  const { state } = useTravelPlannerState();
  return <output data-testid="travel-state">{JSON.stringify({
    document: state.activeTripDocument,
    days: state.tripWorkspace.days?.length || 0,
  })}</output>;
};

describe('历史正式方案恢复', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, json: async () => ({ servers: [], skills: [], cases: [] }),
    })));
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('应用成功后立即刷新，以服务端无草稿为准并清除较高修订号的旧镜像', async () => {
    const formal = { ...formalFixture, revision: 2 };
    const restored = restoreFormalSnapshotState({ current: { document: formal } })!;
    writeTripEditDraftMirror('applied-trip', createTripEditDraft({ ...formalFixture, revision: 12, title: '已应用的旧草稿' }, []));
    render(
      <TravelPlannerStateProvider>
        <ChatInterface
          currentChatId="applied-trip"
          loadedMessages={[]}
          loadedTripDocument={restored.document}
          loadedTripPlan={restored.plan}
          loadedTripWorkspace={restored.workspace as unknown as Record<string, unknown>}
          loadedTripDraft={null}
        />
        <StateProbe />
      </TravelPlannerStateProvider>,
    );
    await waitFor(() => {
      const state = JSON.parse(screen.getByTestId('travel-state').textContent || '{}');
      expect(state.document?.revision).toBe(2);
      expect(state.document?.title).toBe(formal.title);
      expect(readTripEditDraftMirror('applied-trip')).toBeNull();
    });
  });

  it('没有聊天消息的正式快照仍恢复工作台，切换新会话才清空', async () => {
    const restored = restoreFormalSnapshotState({ current: { document: formalFixture } });
    expect(restored).not.toBeNull();
    const view = (hasSnapshot: boolean) => (
      <TravelPlannerStateProvider>
        <ChatInterface
          currentChatId={hasSnapshot ? 'formal-without-messages' : 'new-trip'}
          loadedMessages={[]}
          loadedTripDocument={hasSnapshot ? restored!.document : null}
          loadedTripPlan={hasSnapshot ? restored!.plan : null}
          loadedTripWorkspace={hasSnapshot ? restored!.workspace as unknown as Record<string, unknown> : null}
        />
        <StateProbe />
      </TravelPlannerStateProvider>
    );
    const { rerender } = render(view(true));
    await waitFor(() => {
      const state = JSON.parse(screen.getByTestId('travel-state').textContent || '{}');
      expect(state.document?.plan_id).toBe(formalFixture.plan_id);
      expect(state.document?.revision).toBe(formalFixture.revision);
      expect(state.days).toBe(formalFixture.itinerary.days.length);
    });
    expect(screen.queryByTestId('create-form')).toBeNull();
    expect(document.querySelector('.formal-plan-bubble')).toBeTruthy();
    expect(screen.getByText(formalFixture.title)).toBeTruthy();
    expect(document.querySelector('.user-message')).toBeNull();

    rerender(view(false));
    await waitFor(() => {
      expect(JSON.parse(screen.getByTestId('travel-state').textContent || '{}')).toEqual({ document: null, days: 0 });
      expect(screen.getByTestId('create-form')).toBeTruthy();
    });
  });
});
