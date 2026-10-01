import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Tag, Typography } from 'antd';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  DownOutlined,
  ExclamationCircleFilled,
  LoadingOutlined,
  ReloadOutlined,
  UpOutlined,
} from '@ant-design/icons';
import type { PlanningStage } from '../state/travelPlannerTypes';
import type { PlanningStatus } from '../../../components/planningState';

const { Text } = Typography;

export type AgentStageStatus = 'pending' | 'running' | 'retrying' | 'completed' | 'degraded' | 'failed';
export type AgentRunStatus = 'planning' | 'completed' | 'cancelled' | 'error';

export interface AgentTimelineStage {
  taskId?: string;
  attempt?: number;
  stage: PlanningStage;
  agentName: string;
  task: string;
  dataSources: string[];
  summary: string | null;
  durationMs: number | null;
  status: AgentStageStatus;
}

export interface AgentRunTimelineState {
  tasks?: Record<string, AgentTimelineStage>;
  requestId: string;
  runId: string | null;
  status: AgentRunStatus;
  notice: string | null;
  stages: Record<PlanningStage, AgentTimelineStage>;
}

export interface PlanningTimelineEvent {
  type: string;
  run_id?: string;
  request_id?: string;
  payload?: Record<string, unknown>;
}

const stageDefinitions: Array<Pick<AgentTimelineStage, 'stage' | 'agentName' | 'task'>> = [
  { stage: 'requirements_analysis', agentName: '需求分析 Agent', task: '整理旅行约束' },
  { stage: 'research', agentName: '资料研究 Agent', task: '研究交通、住宿、美食与目的地资料' },
  { stage: 'route_planning', agentName: '路线 Agent', task: '编排行程、餐饮、预算与路线' },
  { stage: 'realtime_verification', agentName: '实时核验 Agent', task: '核验地点、路线与强实时事实' },
  { stage: 'validation_completed', agentName: '质量 Agent', task: '执行完整 Schema 与业务校验' },
];

const stageLabels: Record<PlanningStage, string> = {
  requirements_analysis: '需求整理',
  research: '资料研究',
  route_planning: '行程编排',
  realtime_verification: '实时核验',
  validation_completed: '质量校验',
};

const stageStatusLabels: Record<AgentStageStatus, string> = {
  pending: '等待中',
  running: '进行中',
  retrying: '正在重试',
  completed: '已完成',
  degraded: '部分信息待确认',
  failed: '未完成',
};

const statusColor: Record<AgentStageStatus, string> = {
  pending: 'default',
  running: 'processing',
  retrying: 'warning',
  completed: 'success',
  degraded: 'warning',
  failed: 'error',
};

const isPlanningStage = (value: unknown): value is PlanningStage => (
  typeof value === 'string' && Object.prototype.hasOwnProperty.call(stageLabels, value)
);

const normalizeStringList = (value: unknown): string[] => (
  Array.isArray(value)
    ? Array.from(new Set(value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map((item) => item.trim())))
    : []
);

const normalizeStageStatus = (value: unknown): AgentStageStatus => (
  value === 'running' || value === 'retrying' || value === 'completed' || value === 'degraded' || value === 'failed'
    ? value
    : 'pending'
);

const safeText = (value: unknown, limit: number): string => (
  typeof value === 'string' ? value.trim().slice(0, limit) : ''
);

const safeDuration = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const duration = Number(value);
  return Number.isFinite(duration) && duration >= 0 && duration <= 86_400_000
    ? duration
    : null;
};

const safeStageSources = (value: unknown): string[] => (
  normalizeStringList(value)
    .map((item) => item.slice(0, 120))
    .filter(Boolean)
    .slice(0, 3)
);

const createStage = (definition: Pick<AgentTimelineStage, 'stage' | 'agentName' | 'task'>): AgentTimelineStage => ({
  ...definition,
  dataSources: [],
  summary: null,
  durationMs: null,
  status: 'pending',
});

export const createAgentRunTimeline = (requestId: string): AgentRunTimelineState => ({
  requestId,
  runId: null,
  status: 'planning',
  notice: null,
  stages: Object.fromEntries(stageDefinitions.map((definition) => [definition.stage, createStage(definition)])) as Record<PlanningStage, AgentTimelineStage>,
  tasks: {},
});

/**
 * 仅保存用户可见的阶段状态，不保存模型原始推理、工具参数或完整 SSE 事件。
 */
export const serializeAgentRunTimeline = (
  timeline: AgentRunTimelineState | null | undefined,
): Record<string, unknown> | null => {
  if (!timeline || !safeText(timeline.requestId, 160)) return null;
  return {
    schema_version: 2,
    request_id: safeText(timeline.requestId, 160),
    run_id: safeText(timeline.runId, 160) || null,
    status: timeline.status,
    tasks: Object.fromEntries(Object.entries(timeline.tasks || {}).slice(0, 20).map(([key, stage]) => [key, {
      stage: stage.stage, task_id: safeText(stage.taskId, 80), agent_name: safeText(stage.agentName, 120),
      task: safeText(stage.task, 240), data_sources: safeStageSources(stage.dataSources),
      summary: safeText(stage.summary, 360) || null, duration_ms: safeDuration(stage.durationMs),
      status: normalizeStageStatus(stage.status), attempt: stage.attempt,
    }])),
    stages: Object.fromEntries(stageDefinitions.map((definition) => {
      const stage = timeline.stages[definition.stage];
      return [definition.stage, {
        agent_name: safeText(stage?.agentName, 120) || definition.agentName,
        task: safeText(stage?.task, 240) || definition.task,
        data_sources: safeStageSources(stage?.dataSources),
        summary: safeText(stage?.summary, 360) || null,
        duration_ms: safeDuration(stage?.durationMs),
        status: normalizeStageStatus(stage?.status),
      }];
    })),
  };
};

export const restoreAgentRunTimeline = (value: unknown): AgentRunTimelineState | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  const requestId = safeText(payload.request_id, 160);
  if (!requestId) return null;

  const status: AgentRunStatus = payload.status === 'completed'
    || payload.status === 'cancelled'
    || payload.status === 'error'
    ? payload.status
    : 'planning';
  const stagePayloads = payload.stages && typeof payload.stages === 'object' && !Array.isArray(payload.stages)
    ? payload.stages as Record<string, unknown>
    : {};
  const timeline = createAgentRunTimeline(requestId);

  return {
    ...timeline,
    runId: safeText(payload.run_id, 160) || null,
    status,
    tasks: Object.fromEntries(Object.entries(payload.tasks && typeof payload.tasks === 'object' ? payload.tasks : {}).slice(0, 20)
      .filter(([, task]) => task && typeof task === 'object' && isPlanningStage(task.stage))
      .map(([id, task]) => [id, {
        taskId: safeText(id, 80), stage: task.stage, agentName: safeText(task.agent_name, 120),
        task: safeText(task.task, 240), dataSources: safeStageSources(task.data_sources),
        summary: safeText(task.summary, 360) || null, durationMs: safeDuration(task.duration_ms),
        status: normalizeStageStatus(task.status), attempt: Number(task.attempt) || 1,
      }])),
    stages: Object.fromEntries(stageDefinitions.map((definition) => {
      const raw = stagePayloads[definition.stage];
      const stage = raw && typeof raw === 'object' && !Array.isArray(raw)
        ? raw as Record<string, unknown>
        : {};
      return [definition.stage, {
        stage: definition.stage,
        agentName: safeText(stage.agent_name, 120) || definition.agentName,
        task: safeText(stage.task, 240) || definition.task,
        dataSources: safeStageSources(stage.data_sources),
        summary: safeText(stage.summary, 360) || null,
        durationMs: safeDuration(stage.duration_ms),
        status: normalizeStageStatus(stage.status),
      }];
    })) as Record<PlanningStage, AgentTimelineStage>,
  };
};

const updateStage = (
  timeline: AgentRunTimelineState,
  stage: PlanningStage,
  patch: Partial<AgentTimelineStage>,
): AgentRunTimelineState => ({
  ...timeline,
  stages: {
    ...timeline.stages,
    [stage]: {
      ...timeline.stages[stage],
      ...patch,
      dataSources: patch.dataSources || timeline.stages[stage].dataSources,
    },
  },
});

export const applyPlanningTimelineEvent = (
  current: AgentRunTimelineState,
  event: PlanningTimelineEvent,
): AgentRunTimelineState => {
  const payload = event.payload || {};
  const runId = typeof event.run_id === 'string' ? event.run_id : current.runId;

  if (event.type === 'run_started') {
    return { ...current, runId, status: 'planning', notice: null };
  }

  if (event.type === 'agent_stage_started' || event.type === 'agent_stage_updated'
    || event.type === 'agent_stage_retrying' || event.type === 'agent_stage_completed') {
    if (!isPlanningStage(payload.stage)) return current;
    const taskId = safeText(payload.task_id, 80);
    const dataSources = normalizeStringList(payload.data_sources);
    const status = payload.status === 'retrying' ? 'retrying' : event.type === 'agent_stage_started'
      ? 'running'
      : event.type === 'agent_stage_retrying'
        ? 'retrying'
        : normalizeStageStatus(payload.status);
    const patch: Partial<AgentTimelineStage> = {
      agentName: typeof payload.agent_name === 'string' ? payload.agent_name : current.stages[payload.stage].agentName,
      task: typeof payload.task === 'string' ? payload.task : current.stages[payload.stage].task,
      dataSources: dataSources.length > 0 ? dataSources : current.stages[payload.stage].dataSources,
      summary: typeof payload.summary === 'string' && payload.summary.trim() ? payload.summary.trim() : null,
      durationMs: safeDuration(payload.duration_ms),
      status,
    };
    const next = updateStage({ ...current, runId }, payload.stage, patch);
    if (taskId) next.tasks = { ...current.tasks, [taskId]: {
      ...next.stages[payload.stage], taskId, attempt: Number(payload.attempt) || 1,
    } };
    return next;
  }

  if (event.type === 'provider_queue_waiting') {
    const provider = typeof payload.provider === 'string' ? payload.provider.trim() : '';
    const operation = typeof payload.operation === 'string' ? payload.operation.trim() : '';
    const queuePosition = Number(payload.queue_position);
    const summary = Number.isFinite(queuePosition)
      ? `地点核验排队中，前方第 ${queuePosition} 位`
      : '地点核验排队中';
    return updateStage({ ...current, runId }, 'realtime_verification', {
      status: 'running',
      summary,
      dataSources: [provider, operation].filter(Boolean),
    });
  }

  if (event.type === 'run_soft_timeout') {
    return {
      ...current,
      runId,
      notice: typeof payload.message === 'string' && payload.message.trim() ? payload.message.trim() : current.notice,
    };
  }

  if (event.type === 'trip_plan_completed' || event.type === 'plan_revision_completed') {
    return { ...current, runId, status: 'completed' };
  }

  if (event.type === 'run_cancelled') {
    return { ...current, runId, status: 'cancelled' };
  }

  if (event.type === 'error') {
    return { ...current, runId, status: 'error' };
  }

  return current;
};

const formatDuration = (durationMs: number | null): string | null => {
  if (!durationMs || durationMs <= 0) return null;
  if (durationMs < 1_000) return '不足 1 秒';
  return `${(durationMs / 1_000).toFixed(durationMs >= 10_000 ? 0 : 1)} 秒`;
};

const stageIcon = (status: AgentStageStatus) => {
  if (status === 'completed') return <CheckCircleFilled />;
  if (status === 'degraded' || status === 'failed') return <ExclamationCircleFilled />;
  if (status === 'running') return <LoadingOutlined spin />;
  if (status === 'retrying') return <ReloadOutlined />;
  return <ClockCircleOutlined />;
};

export interface AgentRunTimelineProps {
  timeline: AgentRunTimelineState;
  planningStatus: PlanningStatus;
}

export const AgentRunTimeline = ({ timeline, planningStatus }: AgentRunTimelineProps) => {
  const terminal = timeline.status !== 'planning' || planningStatus !== 'planning';
  const [expanded, setExpanded] = useState(!terminal);
  const previousRequestId = useRef(timeline.requestId);
  const visibleTasks = useMemo(() => Object.keys(timeline.tasks || {}).length
    ? Object.values(timeline.tasks!) : Object.values(timeline.stages), [timeline.tasks, timeline.stages]);
  const completedCount = useMemo(
    () => visibleTasks.filter((stage) => stage.status === 'completed' || stage.status === 'degraded').length,
    [visibleTasks],
  );
  const activeStage = useMemo(
    () => visibleTasks.find((stage) => stage.status === 'running' || stage.status === 'retrying') || null,
    [visibleTasks],
  );

  useEffect(() => {
    if (previousRequestId.current === timeline.requestId) return;
    previousRequestId.current = timeline.requestId;
    setExpanded(true);
  }, [timeline.requestId]);

  useEffect(() => {
    if (terminal) setExpanded(false);
  }, [terminal]);

  const statusText = terminal
    ? timeline.status === 'completed'
      ? `规划流程已结束 · 已完成 ${completedCount}/${visibleTasks.length} 项任务`
      : timeline.status === 'cancelled'
        ? '本次规划已停止，保留已完成阶段'
        : '本次规划未完成，保留已完成阶段'
    : activeStage
      ? `正在执行：${stageLabels[activeStage.stage]}`
      : '正在启动规划流程';

  return (
    <section
      aria-label="智能体规划过程"
      className="agent-run-timeline"
    >
      <div className="agent-run-timeline__header">
        <div className="agent-run-timeline__heading">
          <Text strong className="agent-run-timeline__title">智能体规划过程</Text>
          <Text className="agent-run-timeline__status">{statusText}</Text>
        </div>
        <Button
          type="text"
          size="small"
          aria-expanded={expanded}
          aria-label={expanded ? '收起智能体规划过程' : '展开智能体规划过程'}
          onClick={() => setExpanded((value) => !value)}
          icon={expanded ? <UpOutlined /> : <DownOutlined />}
        >
          {expanded ? '收起' : '查看'}
        </Button>
      </div>

      {expanded && (
        <div className="agent-run-timeline__body">
          <ol className="agent-run-timeline__steps">
            {visibleTasks.map((stage, index) => {
              const duration = formatDuration(stage.durationMs);
              return (
                <li
                  key={stage.taskId || stage.stage}
                  className="agent-run-timeline__step"
                  data-agent-status={stage.status}
                >
                  <span
                    aria-hidden="true"
                    className="agent-run-timeline__marker"
                  >
                    {stage.status === 'pending' ? index + 1 : stageIcon(stage.status)}
                  </span>
                  <div className="agent-run-timeline__stage-copy">
                    <div className="agent-run-timeline__stage-heading">
                      <Text strong className="agent-run-timeline__stage-name">{stage.taskId ? stage.agentName : stageLabels[stage.stage]}</Text>
                      {(stage.attempt || 0) > 1 && <Text>第 {stage.attempt} 次尝试</Text>}
                      <Tag color={statusColor[stage.status]} className="agent-run-timeline__tag">{stageStatusLabels[stage.status]}</Tag>
                      {duration && <Text className="agent-run-timeline__duration">{duration}</Text>}
                    </div>
                    <Text className="agent-run-timeline__task">{stage.agentName} · {stage.task}</Text>
                    <div className="agent-run-timeline__summary" aria-label={`${stage.agentName}的阶段结论`}>
                      <span>阶段结论</span>
                      <Text>{stage.summary || (stage.status === 'pending' ? '等待前序信息就绪。' : '正在整理可验证的阶段结果。')}</Text>
                    </div>
                    {stage.dataSources.length > 0 && (
                      <div className="agent-run-timeline__sources" aria-label="本阶段数据源">
                        <span>数据源</span>
                        {stage.dataSources.slice(0, 3).map((source) => (
                          <Tag key={source} className="agent-run-timeline__source">{source}</Tag>
                        ))}
                        {stage.dataSources.length > 3 && <Tag className="agent-run-timeline__source">+{stage.dataSources.length - 3}</Tag>}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
          {timeline.notice && (
            <Text className="agent-run-timeline__notice">
              {timeline.notice}
            </Text>
          )}
        </div>
      )}
    </section>
  );
};

export default AgentRunTimeline;
