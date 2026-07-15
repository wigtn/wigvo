# Contributing — WIGVO CI & 협업 공통규약

여러 명이 동시에 작업하면서 **머지 충돌을 줄이고 빠르게** 가기 위한 규약입니다.
데모 전 크런치 기간에는 특히 §0와 §4(충돌 방지)를 우선합니다.

소통은 한국어, **코드·커밋·PR 제목은 영어**.

---

## 0. 크런치 4원칙 (제일 중요)

1. **작게, 자주.** PR 하나 = 관심사 하나. 가급적 ~400줄 이하. 큰 작업은 쪼개서 먼저 머지.
2. **항상 최신 main에서 분기, 매일 동기화.** 작업 시작 전 `git fetch && git rebase origin/main`(또는 merge). 오래 묵힌 브랜치가 충돌의 주범.
3. **PR 전 로컬 게이트 통과** (§2). 깨진 채로 올리지 않는다.
4. **무관한 코드 건드리지 않기.** drive-by 리포맷·정리·import 재정렬 금지 — diff가 커지고 충돌이 폭증한다. 포맷은 별도 PR.

---

## 1. 브랜치 & 커밋

- 분기: **항상 `origin/main`에서.** 머지/닫힌 PR이 달린 브랜치 재사용 금지(push해도 PR 재오픈 안 됨).
- 브랜치명: `<type>/<kebab-summary>` — `feat/`, `fix/`, `refactor/`, `docs/`, `chore/`, `test/`.
- 커밋: **Conventional Commits** — `feat(web): ...`, `fix(relay): ...`, `chore(infra): ...`. scope는 `web`/`relay`/`mobile`/`infra`.
- 한 브랜치 = 한 작업 단위. 여러 도메인에 걸치면 PR을 나눈다.

## 2. PR 전 로컬 게이트 (필수)

올리기 전에 **자기 영역**을 로컬에서 통과시킨다. (CI가 같은 걸 자동으로 돌리지만, 로컬 선통과가 리뷰 왕복을 줄인다.)

**Web** (`apps/web` 변경 시):
```bash
cd apps/web
npx tsc --noEmit      # 타입 체크
npm run build         # 프로덕션 빌드
```

**Relay** (`apps/relay-server` 변경 시):
```bash
cd apps/relay-server
uv sync --dev
uv run python -m pytest -q   # 단위 테스트 (현재 442개)
```

> 새 동작/버그 수정은 **테스트와 함께**. relay는 pytest 필수. "나중에 테스트"는 "테스트 안 함"이다.

## 3. PR 규칙

- base = `main`. 제목은 커밋 컨벤션과 동일.
- 본문에 **요약 / 변경 / 테스트 플랜**. 공유 타입·계약 변경 시 명시.
- **리뷰어 지정**: web/UI → **상우(@sonsangwoo1116)**, relay/백엔드 → 해당 담당. 터미널에서 `/review-pr <번호>`.
- **CI green + 1 승인** 후 **squash-merge**.
- 같은 브랜치에 이미 열린 PR이 있으면 새로 만들지 말고 push만(자동 갱신).

## 4. 충돌 방지 — 도메인 / 소유권 맵 (핵심)

### 도메인 경계 (서로 안 넘는다)
| 영역 | 경로 | 담당 |
|---|---|---|
| Frontend (Web) | `apps/web/**` | 상우 (UI 개선 진행 중) |
| Relay (Backend) | `apps/relay-server/**` | 백엔드 |
| Mobile | `apps/mobile/**` | — |
| Infra/CI | `.github/**`, `docker-compose.yml` | 인프라 |

- **현재 상우가 web UI(통화 화면 + `/monitor` 관전 화면)를 작업 중**입니다. 그 동안 다른 사람은 `apps/web/hooks/useRelayCall*`, `apps/web/components/call/**`를 **건드리기 전에 상우와 먼저 조율**하세요.

### Hot files (여러 명이 만지는 곳 — 편집 전 조율, 변경은 additive로)
- `apps/web/hooks/useRelayCall.ts`, `apps/web/hooks/useRelayCallStore.ts`
- `apps/web/shared/call-types.ts` ↔ `apps/relay-server/src/types.py` (**공유 계약**)
- `apps/relay-server/src/call_manager.py`

규칙: 이 파일들은 **기능 추가는 덧붙이는 방식**으로, 기존 구조 대규모 리팩토링은 사전 합의 후 단독 PR로. 리뷰 요청 전 반드시 최신 main 반영.

### 공유 계약 동기화
`call-types.ts`(web)와 `types.py`(relay)의 메시지 타입/필드는 **한 쌍**입니다. 한쪽을 바꾸면 **같은 PR에서 양쪽을 맞추거나**, 불가하면 PR 본문에 후속 작업을 명시하세요. (E.164 전화 검증식처럼 web↔relay가 동일해야 하는 값은 한 곳을 바꾸면 다른 곳도 확인.)

## 5. 머지 프로토콜

1. 리뷰 요청 **전** 최신 main을 rebase/merge → **로컬에서 충돌 해결**.
2. CI green + 승인 → **squash-merge**.
3. 머지되면 다른 작업자는 **즉시 `git fetch && rebase origin/main`** 으로 따라잡는다.
4. 머지 후 stale 브랜치는 삭제.

## 6. CI (자동 게이트)

- `.github/workflows/ci.yml` — PR(및 main push) 시 **변경 영역만** 검사:
  - **web**: `tsc --noEmit` + `next build`
  - **relay**: `pytest`
- **GitHub-hosted 러너(ubuntu-latest)** 에서 실행 → 맥미니 self-hosted 러너와 **무관하게 항상 동작**.
- 배포(`deploy-prod.yml`)는 CI와 **별개**이며 현재 수동(맥미니). 자동배포는 GCP Cloud Run 이전 시 정비 예정.

## 7. 알려진 갭 (개선 백로그, 비차단)

- **web에 ESLint 설정/lockfile 없음** → lint 게이트 보류. `package-lock.json` 추가(재현 가능 설치) + ESLint flat config 도입 권장.
- **web 단위 테스트 러너 없음** → vitest 도입 시 순수함수(전화번호 추출/검증 등) 회귀 테스트 고정.
- **mobile 빌드/타입 게이트 미정** → 추후 `tsc --noEmit` 추가 검토.

## 8. PoC 서버 리팩토링 (WI 시퀀싱 & seam 계약)

> 근거: `poc-refactor/PRD_poc-server-refactor.md` (추적됨 · 팀 공유). **WI 착수 전 정독.**
> (`docs/`는 gitignore라 로컬 전용 — 공유 PRD는 이 추적 폴더가 원본.)
> §4(도메인 소유권 맵)·Hot files 규칙이 여기에도 그대로 적용된다.

### WI 의존 DAG (착수 순서)

- **WI-1** 부하테스트 — ✅ 완료. baseline: `pytest --co -q` 실측 **= 475** (2026-07-15, scaffold 후 · WI-1 완료 시점은 469, CapacityManager +3 · flow_span +3). 이보다 줄면 테스트 삭제 회귀 의심.
- **WI-2** VAD 오프로드 — 독립. 지금 착수 가능 (tenant·cap 무관).
- **WI-5** CapacityManager + 운영 안전장치 — 독립. WI-2와 병행 가능. (기존 아웃바운드 soft-cap 경쟁도 해소.)
- **WI-3** 멀티테넌트 → **WI-4a** 인증 → **WI-4b** Twilio 서명검증 — **순차 사슬** (앞이 뒤의 선행).
- **WI-6** 인바운드 디스패치 — **선행 필수: WI-3 + WI-4a + WI-4b + WI-5 전부.**
- **착수 게이트:** WI-3 전 격리방식(§8-#2) · WI-4a 전 인증방식(§8-#1) · WI-4b 전 callback URL(§8-#3) 확정.
- **권장 분담(2인):** A = WI-2·WI-5 / B = WI-3→4a→4b. WI-6은 두 트랙 수렴 후.

### 브랜치명 (WI별 · 항상 `origin/main`에서 분기)

| WI | 브랜치명 |
| --- | --- |
| scaffold | `chore/poc-scaffold` |
| WI-2 VAD 오프로드 | `feat/wi2-vad-offload` |
| WI-5 용량·운영 | `feat/wi5-capacity-ops` |
| WI-3 멀티테넌트 | `feat/wi3-multitenant` |
| WI-4a 인증 | `feat/wi4a-auth` |
| WI-4b Twilio 서명 | `feat/wi4b-twilio-signature` |
| WI-6 인바운드 | `feat/wi6-inbound-dispatch` |

- **한 브랜치 = 한 WI.** scaffold 머지 **전엔** 아무도 WI 브랜치를 따지 않는다(배관 충돌 방지).
- 시작: `git checkout main && git pull` → `git checkout -b <브랜치명> origin/main`.

### seam 계약 (인터페이스는 여기서 고정 — 구현은 담당 WI, 표류 금지)

scaffold PR이 아래 seam을 **동작 보존 상태로 먼저 착지**시킨다. 각 WI는 **빈칸(behavior)만** 채운다.

- **`CapacityManager`** (owner WI-5 · 소비: 아웃바운드 `/calls/start`, WI-6 인바운드 claim)
  - `async reserve(call_id) -> bool` — `active + reserved < cap` 원자 확인 + 예약, 초과 시 `False`
  - `commit(call_id)` — reserved → active
  - `release(call_id)` — 예약·활성 해제 (**idempotent**, 모든 실패·취소 경로에서 호출)
  - **불변식: `active + reserved ≤ MAX_CONCURRENT_CALLS`.** scaffold가 기존 `calls.py` soft-cap을 이 경로로 교체.
- **`resolve_outbound_number(tenant_id: str) -> str`** (owner WI-3 · 소비: `outbound.py`, WI-6)
  - scaffold: 현행 단일번호 반환(동작 동일). WI-3: `tenant_call_config` 조회로 교체. **호출부 불변.**
- **`tenant_id` 관통** (owner WI-3)
  - `logging_config`에 `tenant_id_var` 추가 · `CallStartRequest`/`ActiveCall`에 `tenant_id` 필드 · `persist_call`/`update_call`까지 전달. 미해석 요청은 **fail-closed**.
- **flow tracing** (seam · FR-5.1 · owner 전 WI): 각 WI 흐름을 `tracer.flow_span("wiN.flow.step", call_id=..., state=...)`로 감싼다. **제어 흐름만**(id·state·duration·수치) — transcript·전화번호·이름·프롬프트 attr **금지**(§7 Langfuse 프라이버시 게이트). 키 없으면 no-op, 헬퍼가 위험 키를 드롭.
- (WI-6 **내부 전용**) 인바운드 디스패치 상태머신·`inbound_call_dispatch` 테이블은 **WI-6 브랜치에서** — scaffold 아님.

### 머지 케이던스

- **매일 18:00 이후 리뷰+머지 배치**, 또는 서로 연락해 **즉시 리뷰+머지**.
- 나머지는 §1~§5 규약 그대로 (origin/main 분기 · squash-merge · CI green + 1 승인).
