# ACL 2026 논문 최종 개선 계획

**파일**: `docs/paper/acl2026_wigvo.tex`
**데드라인**: Feb 27, 2026 UTC-12
**현재 상태**: SSW 머지 완료 (4d410b6), 컴파일 성공, 8페이지

---

## 1. 논리 구조 개선 (코드 수정)

### 1-1. §4.2 RFC 3389 문단 중복 축약

**위치**: L152 (Echo Gate v2: Silence Injection 끝 문단)

**문제**: "audio drop → VAD deadlock" 논점이 §4.2, §5 intro, §5.1 item 3에서 3회 반복.

**현재 (5줄)**:
```latex
While comfort noise generation (RFC~3389; \citealt{rfc3389_cn_2002}) addresses
perceptual continuity for human listeners in VoIP, we identify a distinct failure
mode: echo-suppression-induced audio gaps cause streaming VAD state machine
deadlock in neural speech APIs. Specifically, if the relay simply \emph{drops}
audio during echo windows rather than injecting silence, the Realtime API's
server-side VAD never observes a silence-to-speech transition and remains stuck
in a ``speaking'' state indefinitely. To our knowledge, this failure mode and
its mitigation via silence injection have not been reported in the context of
neural streaming S2ST pipelines.
```

**개선안 (3줄)**:
```latex
Unlike comfort noise generation (RFC~3389; \citealt{rfc3389_cn_2002}), which
targets human perceptual continuity, our silence injection targets the VAD state
machine of neural speech APIs: dropping frames instead of injecting silence
causes the streaming VAD to deadlock (Section~5.1 details this failure mode).
To our knowledge, this mitigation has not been reported in the context of neural
streaming S2ST pipelines.
```

**효과**:
- 5줄 → 3줄 축약
- §5.1로 forward reference 추가 → 역할 분리 명확
- "To our knowledge" novelty claim 유지

---

### 1-2. §5 섹션 제목 변경

**위치**: L161

**문제**: §5.3 STT Hallucination Blocklist가 VAD가 아닌 ASR 후처리인데, 섹션 제목이 "PSTN-Aware VAD and Robustness"라서 이질적.

**현재**:
```latex
\section{PSTN-Aware VAD and Robustness}
```

**개선안**:
```latex
\section{PSTN-Aware Audio Processing}
```

**효과**:
- §5 intro의 "unified inbound audio pipeline"과 제목 일치
- VAD + STT hallucination 모두 자연스럽게 포함
- 변경 범위: 1줄

---

## 2. Placeholder 교체 (수동 작업)

### 2-1. Figure 1 아키텍처 다이어그램

**위치**: `figures/architecture.tikz` → 직접 디자인한 이미지로 교체

**현재**: inline TikZ 코드 (텍스트 기반 다이어그램)

**필요 작업**:
- 직접 디자인한 아키텍처 다이어그램 이미지 첨부
- TikZ `\input` → `\includegraphics`로 전환 가능 (PNG/PDF)
- 또는 TikZ 유지하고 디자인 반영

**디자인 포함 요소**:
- Browser/App ↔ Relay Server ↔ OpenAI Realtime API
- Session A / Session B 방향 화살표
- Echo path (점선)
- Twilio Media Streams ↔ PSTN Callee
- 오디오 포맷 레이블 (PCM16, G.711 μ-law)

---

### 2-2. screenshot_call.png

**위치**: L280 `\includegraphics[width=\columnwidth]{figures/screenshot_call}`

**필요 작업**:
- wigvo.run에서 V2V 통화 중 스크린샷 촬영
- 최소 1200px 폭, 16:9 또는 4:3
- `figures/screenshot_call.png`으로 저장

---

### 2-3. YouTube 데모 비디오 URL

**위치**: L275 `\url{https://youtu.be/PLACEHOLDER}`

**필요 작업**:
- 실제 테스트 촬영 + 편집
- YouTube 업로드 후 URL 교체

---

## 3. 페이지 수 확인

**현재**: 8페이지 (본문 ~6.5p + Ethics/References ~1.5p)

**ACL System Demos 규정**: 본문 6페이지 + Ethics/References 무제한

**확인 필요**:
- Figure 1이 실제 이미지로 교체되면 크기 변동 가능
- 약점 1 (5줄→3줄) 적용 시 약간의 공간 절약
- 최종 컴파일 후 본문이 6p 이내인지 확인 필요

**만약 초과 시 우선 축소 대상**:
1. §2 Related Work의 full-duplex paragraph (현재 ~10줄, 가장 긴 paragraph)
2. §4.2 결과 문단 (L154, Echo Gate 통계가 §6.2 Table 3과 중복)

---

## 4. 최종 검증 체크리스트

- [ ] 약점 1 적용: §4.2 RFC 3389 문단 축약
- [ ] 약점 2 적용: §5 제목 → "PSTN-Aware Audio Processing"
- [ ] Figure 1: 직접 디자인한 아키텍처 다이어그램 교체
- [ ] screenshot_call.png: 실제 스크린샷 교체
- [ ] YouTube URL: PLACEHOLDER → 실제 URL
- [ ] 컴파일 성공 확인 (tectonic)
- [ ] 본문 6페이지 이내 확인
- [ ] 저자명 표시 확인 (single-blind)
- [ ] 라인번호 표시 확인 (review mode)
- [ ] Figure 1~4 + Table 1~3 참조 resolve 확인
- [ ] references.bib warning 확인 (booktitle 누락 등)
