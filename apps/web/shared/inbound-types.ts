export type InboundDispatchState =
  | 'RINGING'
  | 'WAITING_FOR_AGENT'
  | 'CLAIMED'
  | 'SESSION_STARTING'
  | 'CONNECTED'
  | 'ENDED'
  | 'CANCELLED'
  | 'TIMEOUT'
  | 'REJECTED';

export interface InboundCall {
  call_id: string;
  tenant_id: string;
  state: InboundDispatchState;
  created_at: string;
  updated_at: string;
  languages: string[];
  version: number;
}

export interface InboundPickupResult {
  call_id: string;
  state: 'CONNECTED';
  relay_ws_url: string;
  pickup_token: string;
  role: string;
  source_language: string;
  target_language: string;
  communication_mode: 'voice_to_voice';
  call_mode: 'relay';
}
