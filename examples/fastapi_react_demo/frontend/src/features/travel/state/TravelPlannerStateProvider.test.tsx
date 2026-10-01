import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { setTravelPlannerField } from './travelPlannerReducer';
import {
  TravelPlannerStateProvider,
  useTravelPlannerState,
} from './TravelPlannerStateProvider';

const StateProbe = () => {
  const { state, dispatch } = useTravelPlannerState();
  return (
    <>
      <output>{state.showMap ? '地图已打开' : '地图已关闭'}</output>
      <button onClick={() => dispatch(setTravelPlannerField('showMap', true))}>打开地图</button>
    </>
  );
};

describe('TravelPlannerStateProvider', () => {
  it('provides one reducer-backed planner state to descendants', () => {
    render(
      <TravelPlannerStateProvider>
        <StateProbe />
      </TravelPlannerStateProvider>,
    );

    expect(screen.getByText('地图已关闭')).toBeTruthy();
    act(() => screen.getByRole('button', { name: '打开地图' }).click());
    expect(screen.getByText('地图已打开')).toBeTruthy();
  });
});
