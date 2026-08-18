# AGENTS.md — APrIce 에이전트 작업 규칙

Claude Code / Codex 등 코딩 에이전트가 이 저장소에서 작업할 때 지키는 규칙입니다.
**이 파일이 규칙의 단일 원본입니다.** `CLAUDE.md`는 이 파일을 가리키기만 합니다.

사람이 읽을 배경 설명은 [`docs/onboarding.md`](docs/onboarding.md),
모듈 간 계약은 [`docs/architecture.md`](docs/architecture.md)에 있습니다.

---

## 1. 이 프로젝트가 하는 일

소스코드를 **AST로 파싱**해서 유료 API 호출을 찾아내고, 요청당 비용을 **범위로**
보고합니다. 정적 분석기입니다.

---

## 2. 절대 규칙 — 근거 없는 숫자를 만들지 않는다

**이 프로젝트에서 가장 중요한 규칙입니다.**

정적 분석으로는 **어떤 코드 줄이 몇 번 실행되는지 알 수 없습니다.** 그래서
APrIce는 월 비용·트래픽·요청 횟수를 **추정하지 않습니다.** 못 하는 게 아니라
하지 않기로 한 설계 결정입니다.

금지 사항:

- 월 청구액 / 연간 비용 / 예상 트래픽을 계산하는 기능 추가
- 호출 횟수를 임의의 기본값(예: "하루 1000회")으로 가정
- 범위(`low`–`high`)를 단일 숫자로 축약해서 대표값처럼 표시
- 가격 정보를 추측해서 YAML에 기입 (출처 링크 없는 가격은 머지 불가)

허용되는 가정은 **사용자가 눈으로 볼 수 있고 바꿀 수 있는 것뿐**입니다.
예: `--input-tokens` (기본 1000, 리포트에 명시됨).

> 에이전트는 "더 유용하게" 만들려고 월 비용 추정 기능을 자발적으로 추가하는
> 경향이 있습니다. **하지 마세요.** 이건 미완성이 아니라 의도된 경계입니다.
> 자세한 근거: [`docs/methodology.md`](docs/methodology.md)

---

## 3. 담당별 파일 소유 경계

팀원 3명이 각자 에이전트를 병렬로 돌립니다. **자기 소유가 아닌 파일은 수정하지
않습니다.** 필요하면 이슈를 열고 담당자에게 넘깁니다.

| 담당 | 소유 파일 | 지시서 |
|---|---|---|
| **A — 탐지 엔진** | `src/aprice/detector.py`, `src/aprice/rules.py`, `tests/test_detector.py`, `tests/fixtures/` | [`docs/tasks/A.md`](docs/tasks/A.md) |
| **B — 가격 DB** | `src/aprice/pricing.py`, `src/aprice/prices/*.yaml`, `tests/test_pricing.py` | [`docs/tasks/B.md`](docs/tasks/B.md) |
| **C — 출력·CI·문서** | `src/aprice/report.py`, `src/aprice/cli.py`, `.github/`, `docs/`, `README.md` | [`docs/tasks/C.md`](docs/tasks/C.md) |

### 공용 파일 — 혼자 고치지 말 것

- **`src/aprice/models.py`** — 세 모듈이 전부 의존합니다. 여기 필드를 추가하거나
  이름을 바꾸면 나머지 두 명의 작업이 즉시 깨집니다.
  **반드시 이슈를 먼저 열고 3명이 합의한 뒤에만** 변경합니다.
- **`pyproject.toml`** — 의존성 추가 시 이슈로 알립니다.

---

## 4. 작업 흐름

```
이슈 생성 → 브랜치 → 작업 → pytest + ruff → push → PR → 리뷰 1명 → merge
```

- **`main`에 직접 push 금지.** 예외 없습니다.
- **셀프 머지 금지.** 최소 1명 승인 후 머지합니다.
- 브랜치 이름: `feat/…`, `fix/…`, `docs/…`, `test/…`
- 이 규칙은 대회 평가항목(팀워크 6점)이 GitHub Issues/PR/review/merge 이력으로
  채점되기 때문입니다. 나중에 몰아서 만들 수 없는 점수입니다.

### 커밋 메시지

```
feat: TypeScript 호출 탐지 추가
fix: 중첩 루프 깊이 계산 오류 수정
docs: 아키텍처 문서 추가
test: 가격 조회 테스트 보강
refactor: 리포터 렌더링 분리
```

접두어는 `feat` / `fix` / `docs` / `test` / `refactor` 중 하나입니다.

---

## 5. 검증 — PR 올리기 전 반드시 통과

```console
pytest              # 전부 통과해야 함
ruff check .        # 린트
ruff format .       # 포매팅
```

CI(`.github/workflows/ci.yml`)가 같은 걸 돌립니다. 미리 돌려서 왕복을 줄이세요.

**테스트 없는 기능 추가는 머지하지 않습니다.** 평가항목에 "코드가 목적에 맞게
제 기능을 하는 정도"가 명시돼 있고, 테스트가 가장 싼 증명입니다.

---

## 6. 코드 스타일

- Python 3.10+, `from __future__ import annotations` 사용
- 타입 힌트 필수. 데이터 구조는 `models.py`의 frozen dataclass 사용
- **주석은 "무엇"이 아니라 "왜"를 씁니다.** 평가항목에 주석 활용도가 명시돼
  있습니다. 기존 코드의 주석 밀도와 톤을 따라가세요
- 사용자에게 보이는 문자열은 **ASCII만** — 리포트가 Windows 콘솔(cp949)에
  출력됩니다. en-dash(–) 대신 하이픈(-)을 씁니다. `report.py` 참고
- 파일 경로는 슬래시로 정규화 (`ApiCall.location` 참고)

---

## 7. 일정

| 시점 | 상태 |
|---|---|
| **8/29** | 🔒 **기능 동결 — 이후 코드 추가 금지** |
| 8/29 ~ 9/2 | 결과보고서 + 시연 영상 + 문서 정비 |
| 9/3 ~ 9/4 | 1차 서면평가 |

제출물은 **결과보고서 / 소스코드 / 시연 영상** 3종이며 배점이 균등합니다.
코드가 아닌 제출물이 3분의 2입니다.
