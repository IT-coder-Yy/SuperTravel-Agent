import type { TravelPlanDocumentV3 } from './travelPlannerTypes';
import { PlanningEventDeduper } from './planningEventStream';

export interface FormalRevisionStreamEvent extends Record<string, unknown> {
  event_id?: string;
  sequence?: number;
  type?: string;
  request_id?: string;
  document?: unknown;
}

export interface ReadFormalRevisionStreamOptions {
  operationId: string;
  expectedRevision: number;
  deduper: PlanningEventDeduper;
  isFormalDocument: (value: unknown) => value is TravelPlanDocumentV3;
  onPlanningEvent?: (event: FormalRevisionStreamEvent) => void;
}

/** 只接受同一操作、顺序递增且包含完整 V3 文档的修订流终态。 */
export const readFormalRevisionStream = async (
  response: Response,
  options: ReadFormalRevisionStreamOptions,
): Promise<TravelPlanDocumentV3 | null> => {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('无法读取正式版本同步流，请稍后重试。');
  const decoder = new TextDecoder();
  let buffer = '';
  let resolvedDocument: TravelPlanDocumentV3 | null = null;

  const acceptEvent = (event: FormalRevisionStreamEvent) => {
    if (String(event.request_id || '').trim() !== options.operationId || !options.deduper.accept(event)) return;
    if (event.type === 'plan_revision_started' || event.type === 'plan_revision_completed') {
      options.onPlanningEvent?.(event);
      return;
    }
    if (event.type !== 'trip_plan') return;
    if (!options.isFormalDocument(event.document)) {
      throw new Error('正式版本同步内容无效，请刷新后查看。');
    }
    if (event.document.revision !== options.expectedRevision) {
      throw new Error('正式版本已变化，请刷新后查看当前版本。');
    }
    resolvedDocument = event.document;
  };

  const consumeBuffer = (flush = false) => {
    const lines = buffer.split('\n');
    buffer = flush ? '' : (lines.pop() || '');
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line.startsWith('data:')) continue;
      try {
        const event = JSON.parse(line.slice(5).trim());
        if (event && typeof event === 'object' && !Array.isArray(event)) {
          acceptEvent(event as FormalRevisionStreamEvent);
        }
      } catch (error) {
        if (error instanceof Error) throw error;
        throw new Error('正式版本同步内容无法解析，请稍后重试。');
      }
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    consumeBuffer();
    if (resolvedDocument) return resolvedDocument;
  }
  buffer += decoder.decode();
  consumeBuffer(true);
  return resolvedDocument;
};
