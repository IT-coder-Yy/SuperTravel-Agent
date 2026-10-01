export type TravelPartyType = '独自' | '情侣' | '朋友' | '亲子' | '家庭';

export interface TripCreateRequest {
  origin: string;
  destination: string;
  startDate: string;
  endDate: string;
  adults: number;
  children: number;
  seniors: number;
  budget: number;
  partyType?: TravelPartyType;
  preferences: string[];
}
