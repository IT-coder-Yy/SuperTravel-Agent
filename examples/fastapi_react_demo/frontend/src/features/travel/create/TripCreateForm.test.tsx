import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TripCreateForm from './TripCreateForm';

describe('TripCreateForm', () => {
  it('renders every required creation field and a divider before natural-language creation', () => {
    const { container } = render(<TripCreateForm onCreate={() => undefined} />);

    expect(screen.getByRole('heading', { name: '创建一趟新旅程' })).toBeTruthy();
    expect(screen.getByLabelText('出发地')).toBeTruthy();
    expect(screen.getByLabelText('目的地')).toBeTruthy();
    expect(screen.getAllByLabelText('旅行日期')).toHaveLength(2);
    expect(screen.getByLabelText('成人数量')).toBeTruthy();
    expect(screen.getByLabelText('儿童数量')).toBeTruthy();
    expect(screen.getByLabelText('老人数量')).toBeTruthy();
    expect(screen.getByLabelText('总预算')).toBeTruthy();
    expect(container.querySelector('.trip-create-natural-divider')).not.toBeNull();
    expect(container.querySelector('.trip-create-route-connector')).toBeNull();
    expect(screen.queryByText('也可以直接在下方描述旅行需求')).toBeNull();
  });

  it('submits the default three-day form through the shared planning prompt', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<TripCreateForm onCreate={onCreate} />);

    fireEvent.change(screen.getByLabelText('出发地'), { target: { value: '上海' } });
    fireEvent.change(screen.getByLabelText('目的地'), { target: { value: '杭州' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并开始规划' }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    const [request, prompt] = onCreate.mock.calls[0];
    expect(request.origin).toBe('上海');
    expect(request.destination).toBe('杭州');
    expect(request.adults).toBe(2);
    expect(request.budget).toBe(6000);
    expect(prompt).toContain('从上海出发、前往杭州的3天旅行');
  });
});
