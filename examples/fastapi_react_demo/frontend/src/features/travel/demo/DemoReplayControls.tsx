import { Button, Segmented } from 'antd';
import './DemoReplayControls.css';

export type DemoReplayPhase = 'replaying' | 'paused' | 'completed' | 'error';

export interface DemoReplayControlsProps {
  title: string;
  notice: string;
  phase: DemoReplayPhase;
  speed: 1 | 2;
  currentEvent: number;
  totalEvents: number;
  error?: string;
  onPauseResume: () => void;
  onSpeedChange: (speed: 1 | 2) => void;
  onSkipToResult: () => void;
  onRestart: () => void;
  onCreateFromTemplate: () => void;
}

const DemoReplayControls = ({
  title,
  notice,
  phase,
  speed,
  currentEvent,
  totalEvents,
  error,
  onPauseResume,
  onSpeedChange,
  onSkipToResult,
  onRestart,
  onCreateFromTemplate,
}: DemoReplayControlsProps) => {
  const terminal = phase === 'completed' || phase === 'error';
  return (
    <section className="demo-replay-controls" aria-label="示例回放控制">
      <div className="demo-replay-copy">
        <strong>示例回放 · {title}</strong>
        <span>{notice}</span>
        <small>{error || `已播放 ${Math.min(currentEvent, totalEvents)} / ${totalEvents} 个事件`}</small>
      </div>
      <div className="demo-replay-actions">
        {!terminal && <Button size="small" aria-label={phase === 'paused' ? '继续回放' : '暂停回放'} onClick={onPauseResume}>{phase === 'paused' ? '继续' : '暂停'}</Button>}
        <Segmented
          size="small"
          value={speed}
          options={[{ label: '1 倍', value: 1 }, { label: '2 倍', value: 2 }]}
          disabled={terminal}
          onChange={(value) => onSpeedChange(value as 1 | 2)}
        />
        {!terminal && <Button size="small" onClick={onSkipToResult}>跳到结果</Button>}
        <Button size="small" onClick={onRestart}>重新播放</Button>
        {phase === 'completed' && <Button size="small" type="primary" onClick={onCreateFromTemplate}>以此为模板创建行程</Button>}
      </div>
    </section>
  );
};

export default DemoReplayControls;
