# 팀 온보딩 — APrIce

새로 합류했거나, 이 저장소를 처음 여는 사람이 읽는 문서입니다.
**5분 안에 프로젝트를 이해하고, 10분 안에 코드를 돌려보는 것**이 목표입니다.

---

## 1. 우리가 만드는 것

**코드를 읽어서 API 비용을 미리 알려주는 프로그램.**

이런 코드가 있다고 해봅시다.

```python
for user in users:
    client.messages.create(model="claude-opus-5", max_tokens=4096, ...)
```

APrIce를 돌리면 이렇게 나옵니다.

```console
$ aprice scan src/
APrIce: 1 API call(s) found

Cost per request
  src/batch.py:27  claude-opus-5   $0.03572 - $0.10740

Findings
  ! src/batch.py:27  [call-in-loop] API call inside a loop: cost scales with
    the number of iterations, which this tool cannot see.
```

**실행하기 전에, 머지하기 전에** 비용을 알려주는 게 핵심입니다.

### 왜 필요한가

LLM API를 쓰다가 청구서 보고 놀라는 일이 흔합니다. 그런데 기존 도구들은 전부
**"이미 쓴 돈"을 보여줍니다.** 우리는 **"쓰기 전에" 알려줍니다.

### 비슷한 게 이미 있지 않나

있습니다. [llm-cost](https://github.com/rul1an/llm-cost),
[Calcis](https://github.com/marketplace/actions/calcis-llm-cost-estimate). 알고
시작했습니다. 결정적 차이는 이겁니다.

| | 기존 도구 | APrIce |
|---|---|---|
| 분석 대상 | `.prompt` 파일 (별도로 분리해둔 프롬프트) | **소스코드 자체** |
| 프롬프트를 파일로 안 뺀 코드 | ❌ 못 봄 | ✅ 봄 |
| 루프 안 호출 감지 | ❌ | ✅ |

대부분의 실제 코드에는 `.prompt` 파일이 없습니다. 모델명도 프롬프트도 코드
안에 박혀 있죠. **그래서 우리는 코드를 직접 파싱합니다.**

> 이 차별점은 발표와 결과보고서의 핵심 문장이니 팀 전원이 숙지해두세요.

---

## 2. 환경 세팅 (5분)

Python 3.10 이상이 필요합니다.

```console
git clone <저장소 주소>
cd APrIce
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 잘 됐는지 확인

```console
pytest                                    # 17 passed 나오면 성공
aprice scan tests/fixtures/sample_app.py  # 비용 리포트가 출력되면 성공
```

두 개 다 되면 준비 끝입니다.

---

## 3. 저장소 구조

```
APrIce/
├── src/aprice/
│   ├── detector.py      ← 코드에서 API 호출을 찾아냄        (핵심)
│   ├── pricing.py       ← 가격표 조회 + 비용 계산
│   ├── rules.py         ← 위험 패턴 경고
│   ├── report.py        ← 결과 출력 (터미널 / 마크다운)
│   ├── cli.py           ← 명령어 처리
│   ├── models.py        ← 데이터 구조 정의
│   └── prices/*.yaml    ← 가격표 데이터 (코드 아님)
├── tests/               ← 테스트
├── docs/
│   ├── methodology.md   ← 추정 방법론과 한계 (꼭 읽어보세요)
│   └── onboarding.md    ← 이 문서
└── .github/             ← CI 설정, 이슈/PR 템플릿
```

### 각 파일이 하는 일

| 파일 | 역할 | 비유 |
|---|---|---|
| `detector.py` | 코드를 읽어서 API 부르는 곳을 찾음 | 탐지기 |
| `pricing.py` | 가격표 보고 계산 | 계산기 |
| `rules.py` | 위험한 패턴에 경고 | 감시원 |
| `report.py` | 보기 좋게 정리해서 출력 | 보고서 작성 |
| `cli.py` | 사용자 명령을 받아 위 넷을 순서대로 호출 | 접수처 |

동작 순서는 그냥 위에서 아래입니다.

```
코드 파일 → detector → pricing → rules → report → 화면
```

---

## 4. 핵심 아이디어 두 가지

이 두 가지만 이해하면 코드 전체가 읽힙니다.

### (1) 문자열 검색이 아니라 문법 분석 (AST)

`detector.py`는 코드를 **글자로 훑지 않습니다.** 파이썬 내장 `ast` 모듈로
문법 구조를 분석합니다. 그래야 이게 가능하거든요.

- 주석이나 문자열 안에 있는 가짜 매치를 걸러냄
- **이 호출이 for 루프 안에 있는지 알 수 있음** ← 우리 최대 차별점
- `max_tokens=4096` 같은 인자 값을 읽을 수 있음

정규식으로는 셋 다 불가능합니다.

### (2) 단정하지 않고 범위로 말함

우리는 **월 비용을 예측하지 않습니다.** 못 하니까요.

```python
for user in users:      # users가 10개인지 1000만 개인지 코드엔 안 적혀 있음
    client.messages.create(...)
```

호출 횟수는 코드에서 절대 알 수 없습니다. 그래서 알 수 있는 것만 말합니다.

- **요청당 비용** — 모델과 `max_tokens`로 계산 가능 ✅
- **구조적 위험** — "루프 안이라 곱해집니다" 경고 ✅
- ~~월 청구액~~ — 지어내지 않음 ❌

> **이건 프로젝트의 원칙입니다.** 근거 없는 숫자를 넣는 기능은 머지하지
> 않습니다. 자세한 이유는 [`methodology.md`](methodology.md) 참고.

심사위원이 "월 비용은 어떻게 예측하나요?"라고 물으면, **"예측하지 않습니다.
할 수 없기 때문입니다"**가 우리 답입니다. 이게 감점이 아니라 가점 포인트예요.

---

## 5. 협업 규칙 (중요)

대회 1차 평가표에 이렇게 적혀 있습니다.

> **프로젝트 팀워크 (6점)** — github Issues, review, pull requests, commit,
> merge, 커뮤니티 등 관리체계

**나중에 몰아서 만들 수 없는 점수입니다.** 개발 이력 자체가 채점 근거라서,
지금부터 지켜야 합니다. 규칙은 세 개뿐입니다.

### ① 모든 작업은 Issue부터

작업 시작 전에 Issue를 만듭니다. 이슈 번호가 곧 작업 단위입니다.

### ② main에 직접 push 금지

```bash
git checkout -b feat/detect-typescript   # 브랜치 파고
# ... 작업 ...
git push -u origin feat/detect-typescript
# GitHub에서 PR 생성
```

### ③ PR은 다른 사람이 리뷰하고 머지

셀프 머지 금지. 최소 1명 승인 후 머지합니다.

### 커밋 메시지

```
feat: TypeScript 호출 탐지 추가
fix: 중첩 루프 깊이 계산 오류 수정
docs: 온보딩 문서 추가
test: 가격 조회 테스트 보강
```

앞에 `feat` / `fix` / `docs` / `test` / `refactor` 중 하나를 붙입니다.

### PR 올리기 전 체크

```console
pytest              # 테스트 통과
ruff check .        # 코드 검사 통과
ruff format .       # 코드 정렬
```

CI가 자동으로 같은 걸 돌리니, 미리 확인하면 왕복을 줄일 수 있습니다.

---

## 6. 지금 상태와 남은 일

### 되는 것 ✅

- Anthropic / OpenAI / Google API 호출 탐지
- 요청당 비용 계산 (범위로)
- 위험 패턴 경고 (루프 안 호출, max_tokens 없음, 모델명이 변수)
- 터미널 + 마크다운 출력
- `--fail-on-warning` 으로 CI 차단
- 테스트 17개, CI 파이프라인

### 남은 것 🚧

| 할 일 | 난이도 | 비고 |
|---|---|---|
| **OpenAI / Google 가격표 채우기** | 쉬움 | 지금 전부 `0.00` 더미값. **코딩 몰라도 가능** |
| **GitHub Action 만들기** | 어려움 | PR에 봇이 댓글 다는 기능. **2주차 핵심** |
| **국내 API 가격표** | 보통 | 카카오맵/네이버클라우드/토스. 우리만의 차별점 |
| **결과보고서 + 시연 영상** | 보통 | 코드만큼 배점 큼. 절대 미루지 말 것 |

### 처음 기여하기 좋은 작업

가격표 채우기부터 하세요. 파일 하나만 고치면 됩니다.

```yaml
# src/aprice/prices/openai.yaml
- id: gpt-4o
  input_per_mtok: 0.0      # ← 공식 페이지 보고 채우기
  output_per_mtok: 0.0     # ← 공식 페이지 보고 채우기
  verified_on: null        # ← 확인한 날짜로 바꾸기
```

**PR에 출처 링크를 꼭 넣어주세요.** 근거 없는 가격은 리뷰가 불가능합니다.

---

## 7. 일정

| 시점 | 할 일 |
|---|---|
| ~ 8/23 | 코어 기능 (탐지 + 계산 + 가격표) |
| ~ 8/28 | GitHub Action, PR 코멘트, 국내 API |
| **8/29** | 🔒 **기능 동결 — 이후 코드 추가 금지** |
| 8/29 ~ 9/2 | 문서, 결과보고서, 시연 영상 |
| 9/3 ~ 9/4 | 1차 서면평가 |
| 9/9 | 합격자 발표 (예정) |

**8/29 동결은 무조건 지킵니다.** 제출물 3종 중 2종(결과보고서, 시연 영상)이
코드가 아닙니다. 여기서 무너지는 팀이 제일 많습니다.

> 출품작 제출 마감일은 공식 요강에서 확인해 위 표에 추가해야 합니다.

평가 기준 전문은 [`1차-서면평가-기준.md`](1차-서면평가-기준.md)에 정리돼
있습니다.

---

## 8. 막히면

- 코드가 왜 이렇게 생겼는지 → [`methodology.md`](methodology.md)
- 기여 방법 상세 → [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- 그 외 → Issue를 만들어서 물어보세요. 질문도 기록으로 남으면 팀워크 점수입니다.
