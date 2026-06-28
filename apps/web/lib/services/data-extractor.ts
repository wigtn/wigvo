// =============================================================================
// Data Extractor — extract structured data from user messages (fallback)
// =============================================================================

import { CollectedData } from '@/shared/types';

export function extractDataFromMessage(
  message: string,
  scenarioType: string | null
): Partial<CollectedData> {
  const result: Partial<CollectedData> = {};
  const m = message.trim();

  // 날짜/시간 패턴
  if (/(오늘|내일|모레|다음\s*주|월|일|오전|오후|\d+시)/.test(m) && m.length <= 30) {
    result.primary_datetime = m;
  }

  // 인원수 패턴
  const partyMatch = m.match(/^(\d+)\s*명$/);
  if (partyMatch) {
    result.party_size = parseInt(partyMatch[1], 10);
  }

  // 예약자 이름 패턴 (2-4자 한글)
  if (
    /^[가-힣]{2,4}$/.test(m) &&
    !/^(오늘|내일|모레|다음|첫번째|두번째)$/.test(m)
  ) {
    result.customer_name = m;
  }

  // 전화번호 패턴 (E.164 국제형식: +국가코드...)
  // 구분문자(공백/-/()/.) 허용 후 정규화하여 E.164로 검증.
  const phoneMatch = m.match(/\+[1-9][\d\s().-]{6,16}/);
  if (phoneMatch) {
    const e164 = phoneMatch[0].replace(/[^\d+]/g, '');
    if (/^\+[1-9]\d{7,14}$/.test(e164)) {
      result.target_phone = e164;
    }
  }

  // target_name 추출: "이름, 전화번호" 또는 "이름 전화번호" 패턴
  // "Heaven Bread, +1 415-555-1234" → target_name = "Heaven Bread"
  // "강남면옥 +82 2-1234-5678" → target_name = "강남면옥"
  if (result.target_phone) {
    const nameBeforePhone = m.match(
      /^([A-Za-z\s]+|[가-힣]+(?:\s?[가-힣]+)*)[,\s]+\+/
    );
    if (nameBeforePhone) {
      const name = nameBeforePhone[1].trim();
      if (name.length >= 2) {
        result.target_name = name;
      }
    }
  }

  // special_request: 일반 요청 패턴 (시나리오 불문)
  // "소금빵 있는지", "재고 물어봐줘", "영업시간 확인해줘" 등
  // 단, "그렇게 해줘", "전화 걸어줘" 같은 확인성 문구는 제외
  if (!result.special_request) {
    const NON_REQUEST_PHRASES = /^(그렇게|전화|네|응|좋아|알겠|확인|됐|맞아|그래|오케이|ㅇㅇ)/;
    const requestMatch = m.match(
      /([가-힣A-Za-z\s]{2,}(?:있는지|있어\??|남았는지|가능한지|되나요))/
    );
    if (requestMatch && !NON_REQUEST_PHRASES.test(requestMatch[1].trim())) {
      const phrase = requestMatch[1]
        .replace(/\s*(물어봐|문의해|확인해|전화해|알려줘).*$/g, '')
        .trim();
      if (phrase && phrase.length >= 2 && phrase.length <= 80) {
        result.special_request = phrase;
      }
    }
  }

  // INQUIRY(재고/가능 여부) 문의 내용 (기존 패턴 — special_request가 아직 없을 때)
  if (!result.special_request && scenarioType === 'INQUIRY') {
    const inquiryMatch = m.match(
      /(?:.*에\s+)?(.+?(?:남았는지|있는지|가능한지|있어|되나요))/
    );
    const phrase = inquiryMatch?.[1]
      ?.replace(/\s*(물어봐|문의해|확인해|전화해).*$/g, '')
      .trim();
    if (phrase && phrase.length >= 2 && phrase.length <= 80) {
      result.special_request = phrase;
    }
  }

  return result;
}
