export interface SequencedPlanningEvent {
  event_id?: string;
  sequence?: number;
}

export class PlanningEventDeduper {
  private readonly eventIds = new Set<string>();
  private lastSequence = 0;

  accept(event: SequencedPlanningEvent): boolean {
    const sequence = Number(event.sequence);
    const eventId = String(event.event_id || '').trim();
    if (eventId && this.eventIds.has(eventId)) return false;
    if (Number.isFinite(sequence) && sequence > 0 && sequence <= this.lastSequence) return false;
    if (eventId) this.eventIds.add(eventId);
    if (Number.isFinite(sequence) && sequence > 0) this.lastSequence = sequence;
    return true;
  }

  cursor(): number {
    return this.lastSequence;
  }
}
