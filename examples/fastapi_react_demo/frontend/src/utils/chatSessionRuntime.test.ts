import { describe, expect, it } from 'vitest';
import {
  appendChatMessage,
  getChatMessages,
  setChatMessages,
} from './chatSessionRuntime';

type TestMessage = {
  id: string;
  content: string;
};

describe('chatSessionRuntime', () => {
  it('keeps messages isolated by chat id', () => {
    let sessions: Record<string, TestMessage[]> = {};

    sessions = setChatMessages(sessions, 'chat-a', [
      { id: 'a-user', content: 'A question' },
    ]);
    sessions = setChatMessages(sessions, 'chat-b', [
      { id: 'b-user', content: 'B question' },
    ]);
    sessions = appendChatMessage(sessions, 'chat-a', {
      id: 'a-progress',
      content: 'A background progress',
    });
    sessions = appendChatMessage(sessions, 'chat-a', {
      id: 'a-final',
      content: 'A final answer',
    });

    expect(getChatMessages(sessions, 'chat-b').map((message) => message.id)).toEqual(['b-user']);
    expect(getChatMessages(sessions, 'chat-a').map((message) => message.id)).toEqual([
      'a-user',
      'a-progress',
      'a-final',
    ]);
  });
});
