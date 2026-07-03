import assert from 'node:assert/strict';
import {
  appendChatMessage,
  getChatMessages,
  setChatMessages,
} from './chatSessionRuntime';

type TestMessage = {
  id: string;
  content: string;
};

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

assert.deepEqual(
  getChatMessages(sessions, 'chat-b').map((message) => message.id),
  ['b-user'],
);
assert.deepEqual(
  getChatMessages(sessions, 'chat-a').map((message) => message.id),
  ['a-user', 'a-progress', 'a-final'],
);
