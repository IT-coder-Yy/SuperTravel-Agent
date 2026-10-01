import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DemoReplayControls from './DemoReplayControls';

const renderControls = (phase: 'replaying' | 'paused' | 'completed' = 'replaying') => {
  const props = {
    title: '杭州三日人文慢游',
    notice: '案例录制时的信息，仅用于演示。',
    phase,
    speed: 1 as const,
    currentEvent: 4,
    totalEvents: 40,
    onPauseResume: vi.fn(),
    onSpeedChange: vi.fn(),
    onSkipToResult: vi.fn(),
    onRestart: vi.fn(),
    onCreateFromTemplate: vi.fn(),
  };
  render(<DemoReplayControls {...props} />);
  return props;
};

describe('DemoReplayControls', () => {
  it('provides pause, speed, final result and restart controls while replaying', () => {
    const props = renderControls();

    expect(screen.getByText('示例回放 · 杭州三日人文慢游')).not.toBeNull();
    expect(screen.getByText('案例录制时的信息，仅用于演示。')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '暂停回放' }));
    fireEvent.click(screen.getByRole('button', { name: '跳到结果' }));
    fireEvent.click(screen.getByRole('button', { name: '重新播放' }));

    expect(props.onPauseResume).toHaveBeenCalledOnce();
    expect(props.onSkipToResult).toHaveBeenCalledOnce();
    expect(props.onRestart).toHaveBeenCalledOnce();
  });

  it('only exposes template creation after replay completion', () => {
    renderControls('completed');

    expect(screen.getByRole('button', { name: '以此为模板创建行程' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: '暂停回放' })).toBeNull();
  });
});
