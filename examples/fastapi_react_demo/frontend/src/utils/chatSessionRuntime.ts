export type ChatMessageMap<TMessage> = Record<string, TMessage[]>;

export const getChatMessages = <TMessage>(
  sessions: ChatMessageMap<TMessage>,
  chatId: string,
): TMessage[] => {
  if (!chatId) return [];
  return sessions[chatId] ?? [];
};

export const setChatMessages = <TMessage>(
  sessions: ChatMessageMap<TMessage>,
  chatId: string,
  messages: TMessage[],
): ChatMessageMap<TMessage> => {
  if (!chatId) return sessions;
  return {
    ...sessions,
    [chatId]: [...messages],
  };
};

export const appendChatMessage = <TMessage>(
  sessions: ChatMessageMap<TMessage>,
  chatId: string,
  message: TMessage,
): ChatMessageMap<TMessage> => {
  if (!chatId) return sessions;
  return setChatMessages(sessions, chatId, [
    ...getChatMessages(sessions, chatId),
    message,
  ]);
};
