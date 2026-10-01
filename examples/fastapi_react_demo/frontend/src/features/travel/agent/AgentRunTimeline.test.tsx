import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  AgentRunTimeline,
  applyPlanningTimelineEvent,
  createAgentRunTimeline,
  restoreAgentRunTimeline,
  serializeAgentRunTimeline,
} from './AgentRunTimeline';

describe('AgentRunTimeline', () => {
  it('keeps parallel specialists and retry attempts separate across history restore', () => {
    let timeline = createAgentRunTimeline('parallel');
    for (const [task_id, agent_name, status, attempt] of [['research', '资料研究', 'running', 1], ['dining', '餐饮研究', 'completed', 1], ['research', '资料研究', 'retrying', 2]] as const) {
      timeline = applyPlanningTimelineEvent(timeline, { type: 'agent_stage_updated', payload: {
        task_id, agent_name, stage: 'research', status, attempt, task: '核验', result: { count: 5 },
      } });
    }
    const restored = restoreAgentRunTimeline(serializeAgentRunTimeline(timeline))!;
    expect(restored.tasks?.dining.status).toBe('completed');
    expect(restored.tasks?.research.status).toBe('retrying');
    expect(restored.tasks?.research.attempt).toBe(2);
    render(<AgentRunTimeline timeline={restored} planningStatus="planning" />);
    expect(screen.getByText('资料研究')).toBeTruthy();
    expect(screen.getByText('餐饮研究')).toBeTruthy();
    expect(screen.getByText('第 2 次尝试')).toBeTruthy();
  });
  it('maps real stage events to the five visible planning stages', () => {
    const initial = createAgentRunTimeline('req-1');
    const updated = applyPlanningTimelineEvent(initial, {
      type: 'agent_stage_completed',
      run_id: 'run-1',
      payload: {
        stage: 'research',
        agent_name: '资料研究 Agent',
        task: '检索目的地资料',
        data_sources: ['本地知识库', '可信网页'],
        summary: '交通、住宿和餐饮资料已汇总',
        duration_ms: 1250,
        status: 'completed',
      },
    });

    expect(updated.runId).toBe('run-1');
    expect(updated.stages.research.status).toBe('completed');
    expect(updated.stages.research.summary).toContain('资料已汇总');
    expect(updated.stages.research.dataSources).toEqual(['本地知识库', '可信网页']);
  });

  it('starts expanded while planning and collapses after completion while remaining reviewable', () => {
    const timeline = createAgentRunTimeline('req-1');
    const { rerender } = render(<AgentRunTimeline timeline={timeline} planningStatus="planning" />);

    expect(screen.getByText('需求整理')).toBeTruthy();
    rerender(<AgentRunTimeline timeline={{ ...timeline, status: 'completed' }} planningStatus="completed" />);

    expect(screen.queryByText('需求整理')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '展开智能体规划过程' }));
    expect(screen.getByText('需求整理')).toBeTruthy();
  });

  it('renders a safe stage conclusion and data source instead of raw tool output', () => {
    const timeline = applyPlanningTimelineEvent(createAgentRunTimeline('req-summary'), {
      type: 'agent_stage_completed',
      payload: {
        stage: 'research',
        status: 'completed',
        summary: '已整理目的地景点、住宿与餐饮的公开参考信息。',
        data_sources: ['地图地点检索', '网页参考'],
      },
    });

    render(<AgentRunTimeline timeline={timeline} planningStatus="planning" />);

    expect(screen.getAllByText('阶段结论')).toHaveLength(5);
    expect(screen.getByText('已整理目的地景点、住宿与餐饮的公开参考信息。')).toBeTruthy();
    expect(screen.getByText('数据源')).toBeTruthy();
    expect(screen.getByText('地图地点检索')).toBeTruthy();
  });

  it('persists only the safe visible stage summary and restores it for history review', () => {
    const timeline = applyPlanningTimelineEvent(createAgentRunTimeline('req-1'), {
      type: 'agent_stage_completed',
      run_id: 'run-1',
      payload: {
        stage: 'research',
        summary: '已汇总公开交通与住宿资料',
        data_sources: ['可信网页'],
        duration_ms: 900,
        status: 'completed',
        hidden_reasoning: 'this must never be stored',
      },
    });
    const persisted = serializeAgentRunTimeline({ ...timeline, status: 'completed', notice: '原始异常详情不应持久化' });
    const restored = restoreAgentRunTimeline(persisted);

    expect(JSON.stringify(persisted)).not.toContain('hidden_reasoning');
    expect(JSON.stringify(persisted)).not.toContain('原始异常详情不应持久化');
    expect(restored?.status).toBe('completed');
    expect(restored?.stages.research.summary).toContain('已汇总公开交通');
  });
});
