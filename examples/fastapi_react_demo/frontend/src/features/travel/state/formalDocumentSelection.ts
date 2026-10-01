import type { TravelPlanDocumentV3 } from './travelPlannerTypes';

export interface FormalDocumentMessageLike {
  formalDocument?: TravelPlanDocumentV3;
  supersededByRevision?: number;
}

export const selectActiveFormalDocument = (
  activeChatId: string,
  documentsByChatId: Record<string, TravelPlanDocumentV3>,
  messages: FormalDocumentMessageLike[],
): TravelPlanDocumentV3 | null => (
  documentsByChatId[activeChatId]
  || [...messages]
    .reverse()
    .find((message) => message.formalDocument && !message.supersededByRevision)
    ?.formalDocument
  || null
);
