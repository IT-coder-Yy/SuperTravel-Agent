import { afterEach, describe, expect, it } from 'vitest';
import formalFixture from '../../../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import {
  createTripEditDraft,
  readTripEditDraftMirror,
  removeTripEditDraftMirror,
  restoreTripEditDraft,
  selectTripEditDocument,
  writeTripEditDraftMirror,
} from './tripEditDraft';

const formalDocument = (): Record<string, unknown> => ({ ...formalFixture, revision: 1, status: 'formal' });

const draftDocument = (): Record<string, unknown> => ({
  ...formalFixture,
  revision: 2,
  status: 'formal',
  title: '杭州契约样本（草稿）',
});

afterEach(() => {
  window.localStorage.clear();
});

describe('tripEditDraft', () => {
  it('历史切换后优先选择页面可见文档而不是滞后的 ref', () => {
    const visible = { ...formalDocument(), plan_id: 'visible-plan' };
    const staleRef = { ...formalDocument(), plan_id: 'previous-plan' };

    expect(selectTripEditDocument(visible, staleRef, null)?.plan_id).toBe('visible-plan');
  });

  it('以正式版本为基线恢复草稿，并保留独立的未应用操作集', () => {
    const draft = createTripEditDraft(draftDocument(), [{
      operation_id: 'edit-note-1',
      type: 'update_activity_note',
      payload: { activity_id: 'fixture-activity-1', content: '草稿备注' },
      base_version: 1,
    }]);

    const restored = restoreTripEditDraft(draft, formalDocument());

    expect(restored?.document.status).toBe('formal');
    expect(restored?.plan.version).toBe(2);
    expect(restored?.workspace.days?.[0]?.revision).toBe(2);
    expect(restored?.operations).toHaveLength(1);
  });

  it('拒绝覆盖其他行程或未超过正式版本的草稿', () => {
    const wrongPlan = { ...draftDocument(), plan_id: 'another-plan' };
    const stale = { ...formalDocument(), status: 'draft' };

    expect(restoreTripEditDraft(createTripEditDraft(wrongPlan, []), formalDocument())).toBeNull();
    expect(restoreTripEditDraft(createTripEditDraft(stale, []), formalDocument())).toBeNull();
  });

  it('浏览器只镜像当前一份草稿，放弃时仅清除对应行程', () => {
    const first = createTripEditDraft(draftDocument(), []);
    const second = createTripEditDraft({ ...draftDocument(), revision: 3 }, []);

    writeTripEditDraftMirror('trip-1', first);
    writeTripEditDraftMirror('trip-2', second);

    expect(readTripEditDraftMirror('trip-1')).toBeNull();
    expect(readTripEditDraftMirror('trip-2')?.document.revision).toBe(3);
    removeTripEditDraftMirror('trip-1');
    expect(readTripEditDraftMirror('trip-2')?.document.revision).toBe(3);
    removeTripEditDraftMirror('trip-2');
    expect(readTripEditDraftMirror('trip-2')).toBeNull();
  });
});
