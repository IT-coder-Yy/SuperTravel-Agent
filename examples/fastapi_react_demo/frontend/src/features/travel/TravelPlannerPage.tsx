import { forwardRef } from 'react';
import ChatInterface, {
  type ChatInterfaceProps,
  type ChatInterfaceRef,
} from '../../components/ChatInterface';
import { TravelPlannerStateProvider } from './state/TravelPlannerStateProvider';

export type TravelPlannerPageRef = ChatInterfaceRef;
export type TravelPlannerPageProps = ChatInterfaceProps;

const TravelPlannerPage = forwardRef<TravelPlannerPageRef, TravelPlannerPageProps>((props, ref) => (
  <TravelPlannerStateProvider>
    <main className="travel-planner-page" aria-label="旅行规划工作区">
      <ChatInterface ref={ref} {...props} />
    </main>
  </TravelPlannerStateProvider>
));

TravelPlannerPage.displayName = 'TravelPlannerPage';

export default TravelPlannerPage;
