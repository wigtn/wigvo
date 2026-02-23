// =============================================================================
// Demo Mode — Entry Point
// =============================================================================
// NEXT_PUBLIC_DEMO_MODE=true 로 활성화
// 화면 녹화용 Mock 데이터로 전체 플로우 시연
// =============================================================================

export function isDemoMode(): boolean {
  // Demo mode is controlled only by env flag.
  // Runtime localStorage toggles are disabled to avoid leaking demo behavior into normal flows.
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem('wigvo_demo_mode');
  }
  return process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
}

export { MOCK_WS_URL_PREFIX } from './mock-ws';
