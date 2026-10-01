import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ClarificationPanel, { ClarificationQuestion } from './ClarificationPanel';

const createQuestion = (
  overrides: Partial<ClarificationQuestion> = {},
): ClarificationQuestion => ({
  id: 'pace-question',
  field: 'pace',
  question: 'Which travel pace do you prefer?',
  reason: 'This keeps the itinerary realistic.',
  options: ['Relaxed', 'Packed'],
  allow_custom: true,
  ...overrides,
});

describe('ClarificationPanel', () => {
  it('renders one question with vertically grouped options and submits the selected value', () => {
    const onSubmit = vi.fn();
    render(
      <ClarificationPanel
        question={createQuestion({ allow_custom: false })}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getAllByText('Which travel pace do you prefer?')).toHaveLength(1);
    const optionGroup = screen.getByRole('group', {
      name: 'Which travel pace do you prefer?的推荐选项',
    });
    expect(optionGroup.classList.contains('clarification-option-row')).toBe(true);
    expect(within(optionGroup).getAllByRole('button')).toHaveLength(2);
    expect(screen.getByText('每次只确认一项；补充后继续分析，必要时在这里显示下一项。')).not.toBeNull();

    const relaxedOption = screen.getByRole('button', { name: '选择“Relaxed”' });
    fireEvent.click(relaxedOption);

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({ pace: 'Relaxed' });
    expect(relaxedOption.getAttribute('aria-pressed')).toBe('true');
  });

  it('accepts, trims, and submits a custom answer with explicit accessible names', () => {
    const onSubmit = vi.fn();
    render(
      <ClarificationPanel
        question={createQuestion({ options: [] })}
        onSubmit={onSubmit}
      />,
    );
    const input = screen.getByRole('textbox', {
      name: 'Which travel pace do you prefer?的自定义答案',
    });
    const submitButton = screen.getByRole('button', {
      name: '提交自定义答案并继续分析',
    });

    expect((submitButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: '  One major sight per day  ' } });
    expect((submitButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(submitButton);

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({ pace: 'One major sight per day' });
    expect(submitButton.textContent).toContain('使用此答案');
  });

  it('shows the profile action only for a usable default and submits its trimmed value', () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ClarificationPanel
        question={createQuestion({ allow_custom: false })}
        profileDefaultValue="  Relaxed  "
        onSubmit={onSubmit}
      />,
    );

    const profileButton = screen.getByRole('button', {
      name: '按常用偏好回答：Relaxed',
    });
    expect(profileButton.textContent).toContain('按常用偏好（Relaxed）');
    fireEvent.click(profileButton);

    expect(onSubmit).toHaveBeenCalledWith({ pace: 'Relaxed' });

    rerender(
      <ClarificationPanel
        question={createQuestion({ id: 'next-question' })}
        profileDefaultValue="   "
        onSubmit={onSubmit}
      />,
    );
    expect(screen.queryByText(/按常用偏好/)).toBeNull();
  });

  it('offers a secondary skip action and prevents duplicate submissions', () => {
    const onSubmit = vi.fn();
    const onSkip = vi.fn();
    render(
      <ClarificationPanel
        question={createQuestion()}
        profileDefaultValue="Relaxed"
        onSubmit={onSubmit}
        onSkip={onSkip}
      />,
    );

    const skipButton = screen.getByRole('button', {
      name: '跳过问题：Which travel pace do you prefer?',
    });
    expect(skipButton.textContent).toContain('跳过此项');

    fireEvent.click(skipButton);
    fireEvent.click(skipButton);

    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    screen.getAllByRole('button').forEach((button) => {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toContain('正在确认这项信息...');
  });

  it('replaces the question in place and resets draft and submission state', () => {
    const onSubmit = vi.fn();
    const onSkip = vi.fn();
    const { rerender } = render(
      <ClarificationPanel
        question={createQuestion()}
        onSubmit={onSubmit}
        onSkip={onSkip}
      />,
    );

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'My own pace' } });
    fireEvent.click(screen.getByRole('button', { name: '提交自定义答案并继续分析' }));

    const nextQuestion = createQuestion({
      id: 'budget-question',
      field: 'budget',
      question: 'What is your budget?',
      reason: 'This helps select suitable options.',
      options: ['Economy', 'Comfort'],
    });
    rerender(
      <ClarificationPanel
        question={nextQuestion}
        answeredCount={1}
        onSubmit={onSubmit}
        onSkip={onSkip}
      />,
    );

    expect(screen.queryByText('Which travel pace do you prefer?')).toBeNull();
    expect(screen.getByText('What is your budget?')).not.toBeNull();
    expect(screen.getByText('已补充 1 项')).not.toBeNull();
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('button', { name: '选择“Economy”' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: '提交自定义答案并继续分析' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '跳过问题：What is your budget?' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('locks every interaction while the parent request is loading', () => {
    render(
      <ClarificationPanel
        question={createQuestion()}
        loading
        profileDefaultValue="Relaxed"
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );

    expect(screen.getByRole('region').getAttribute('aria-busy')).toBe('true');
    screen.getAllByRole('button').forEach((button) => {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true);
  });
});
