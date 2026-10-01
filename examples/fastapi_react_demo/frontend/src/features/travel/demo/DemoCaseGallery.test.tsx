import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DemoCaseGallery, { type DemoReplayCase } from './DemoCaseGallery';

describe('DemoCaseGallery', () => {
  it('shows honest recording states for planned real cases', () => {
    render(<DemoCaseGallery />);

    expect(screen.getByText('点选示例，一键体验完整链路。')).not.toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(3);
    expect(screen.getAllByRole('button').every((button) => button.hasAttribute('disabled'))).toBe(true);
    expect(screen.getAllByText('录制中')).toHaveLength(3);
  });

  it('starts a ready replay directly from the card without a preview dialog', () => {
    const onReplay = vi.fn();
    const readyCase: DemoReplayCase = {
      id: 'accepted-case',
      title: '已验收案例',
      summary: '真实录制事件包。',
      meta: '3 天 · 双人',
      recordingStatus: 'ready',
    };
    render(<DemoCaseGallery cases={[readyCase]} onReplay={onReplay} />);

    fireEvent.click(screen.getByRole('button', { name: '已验收案例，开始示例回放' }));
    expect(onReplay).toHaveBeenCalledWith('accepted-case');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
