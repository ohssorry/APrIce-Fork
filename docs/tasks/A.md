# 담당 A — 탐지 엔진

> 코드를 읽어서 **API 호출을 찾아내고 위험을 판정하는** 부분입니다.
> APrIce의 심장이자 최대 차별점(AST 기반 · 루프 감지)이 여기 있습니다.

## 내 소유 파일

```
src/aprice/detector.py          ← 호출 탐지
src/aprice/rules.py             ← 위험 패턴 판정
tests/test_detector.py
tests/fixtures/                 ← 테스트용 샘플 코드
```

**건드리면 안 되는 파일:** `pricing.py`, `prices/*.yaml`(B 소유) ·
`report.py`, `cli.py`, `.github/`(C 소유) · **`models.py`(공용 🔒 — 이슈 먼저)**

## 작업 순서

| 번호 | 작업 | 난이도 | 선행 |
|---|---|:---:|---|
| A-001 | 누락된 SDK 호출 시그니처 추가 | 쉬움 | — |
| A-002 | `**kwargs` 전달 호출 탐지 규칙 | 쉬움 | — |
| A-003 | 재시도(retry) 패턴 규칙 추가 | 보통 | — |
| A-004 | 루프에서 호출되는 함수 추적 | 어려움 | A-001 |
| A-005 | 탐지 정확도 테스트 픽스처 보강 | 보통 | A-001~003 |

**위에서부터 하나씩.** 하나 끝낼 때마다 PR을 올리고 다음으로 넘어갑니다.
한 PR에 두 개 이상 묶지 마세요 — 리뷰가 안 됩니다.

---

## A-001 — 누락된 SDK 호출 시그니처 추가

**문제.** `detector.py`의 `CALL_SIGNATURES`가 채팅 API만 알고 있습니다. 실제
코드베이스에는 임베딩·이미지·음성 호출이 섞여 있는데, 지금은 **전부 못 봅니다.**
비용이 발생하는 호출인데 리포트에 아예 안 나옵니다.

또 OpenAI 레거시 `client.completions.create()`도 놓칩니다. 현재는
`("chat","completions","create")`만 등록돼 있어서 `chat`이 없는 경로는 매칭
실패합니다.

**할 일.** `CALL_SIGNATURES`에 항목을 추가합니다.

- OpenAI: `embeddings.create`, `images.generate`, `audio.speech.create`,
  `audio.transcriptions.create`, 레거시 `completions.create`
- Anthropic: `messages.batches.create` (배치 API)
- Google: `embed_content`

**주의.** 시그니처는 **점 경로의 꼬리**로 매칭됩니다. 너무 짧게 잡으면
(`("create",)` 같은) 무관한 호출이 전부 걸립니다. 최소 2단계를 유지하세요.
`generate_content`처럼 1단계인 건 이름 자체가 충분히 고유할 때만입니다.

**완료 조건**
- [ ] 새 시그니처마다 `tests/test_detector.py`에 탐지 테스트 1개
- [ ] 기존 테스트 전부 통과 (오탐이 늘지 않았는지 확인)
- [ ] `pytest` · `ruff check .` 통과

> 임베딩·이미지는 `max_tokens`가 없어서 `no-max-tokens` 경고가 뜹니다. 이건
> 지금은 정상 동작으로 두고, 필요하면 A-003에서 다룹니다.

**에이전트에 붙여넣을 프롬프트**

```
AGENTS.md와 docs/architecture.md를 먼저 읽어라.
docs/tasks/A.md의 A-001 작업을 수행한다.
src/aprice/detector.py의 CALL_SIGNATURES에 누락된 SDK 호출을 추가하고,
tests/test_detector.py에 각 시그니처의 탐지 테스트를 추가하라.
detector.py와 tests/test_detector.py 외의 파일은 수정하지 마라.
끝나면 pytest와 ruff check .를 돌려 결과를 보고하라.
```

---

## A-002 — `**kwargs` 전달 호출 탐지 규칙

**문제.** 이런 코드가 흔합니다.

```python
params = {"model": "claude-opus-5", "max_tokens": 4096}
client.messages.create(**params)
```

지금 `detector.py`는 `kw.arg`가 있는 키워드만 읽습니다(`if kw.arg`). `**params`는
`kw.arg`가 `None`이라 걸러지고, 결과적으로 `model=None`, `max_tokens=None`이
됩니다. 호출은 잡히지만 **왜 가격을 못 매기는지 사용자가 알 수 없습니다.**
`model-not-literal`과 `no-max-tokens` 경고만 두 개 뜨는데, 진짜 원인은
"인자를 딕셔너리로 넘겼기 때문"입니다.

**할 일.** 인자가 `**` 언패킹으로 전달됐음을 감지해서 전용 Finding을 냅니다.

- `detector.py`: `ApiCall`에 필드를 추가하지 **말고**(공용 파일 🔒),
  `**` 언패킹 여부를 판단할 방법을 정합니다. 가장 싼 방법은
  `rules.py`가 아니라 `detector.py`에서 판단해 기존 필드로 표현하는 것입니다.
  이게 깔끔하지 않다고 판단되면 **이슈를 열어 `models.py`에 `kwargs_spread: bool`
  추가를 3명에게 제안하세요.** 혼자 고치면 안 됩니다.
- `rules.py`: 새 규칙 `kwargs-spread`, severity `info`.
  메시지 예: "Arguments are passed via ** unpacking, so the model and
  max_tokens are not visible in the source."

**완료 조건**
- [ ] `**params` 호출이 새 규칙 하나로 설명됨
- [ ] 기존 `model-not-literal` / `no-max-tokens`와 중복 경고가 나지 않음
- [ ] 테스트 추가, `pytest` · `ruff check .` 통과

> `models.py` 변경이 필요하다고 판단되면 **작업을 멈추고 이슈를 먼저 여세요.**
> 이게 이 작업의 가장 중요한 판단 지점입니다.

---

## A-003 — 재시도(retry) 패턴 규칙 추가

**문제.** 비용이 조용히 몇 배가 되는 대표적 패턴이 재시도입니다.

```python
for attempt in range(5):
    try:
        return client.messages.create(...)
    except RateLimitError:
        time.sleep(2 ** attempt)
```

지금은 `call-in-loop` 경고가 나가는데, 메시지가 "반복 횟수를 알 수 없다"고
말합니다. **이 경우는 반복 횟수를 알 수 있습니다** — `range(5)`니까 최대 5배입니다.
경고 문구가 틀린 셈입니다.

**할 일.**

1. `rules.py`에 `retry-loop` 규칙 추가 (severity `warn`).
   `try/except`를 감싼 루프 안의 호출을 재시도로 판정합니다.
2. 루프가 `range(<리터럴>)`이면 **반복 상한을 알 수 있으므로** 메시지에 배수를
   명시합니다. 예: "Retry loop bounded at 5 attempts: worst-case cost is 5x."
3. `while True` 재시도는 상한이 없으므로 더 강한 문구를 씁니다.

**설계 주의.** 이건 프로젝트 원칙과 정면으로 맞닿습니다. **`range(5)`는 소스에
적힌 리터럴이므로 "지어낸 숫자"가 아닙니다** — 말해도 됩니다. 반대로 루프 변수가
설정값이면 상한을 모르므로 배수를 말하면 안 됩니다. 이 경계를 지키세요.

**완료 조건**
- [ ] `range(리터럴)` 재시도 → 배수가 메시지에 나옴
- [ ] `while True` 재시도 → 상한 없음이 명시됨
- [ ] 변수 기반 `range(n)` → 배수를 **말하지 않음**
- [ ] `call-in-loop`와 중복되지 않게 정리 (재시도면 재시도 규칙만)
- [ ] 세 경우 각각 테스트, `pytest` · `ruff check .` 통과

---

## A-004 — 루프에서 호출되는 함수 추적

**문제.** 현재 최대 사각지대입니다.

```python
def summarize(doc):
    return client.messages.create(...)   # loop_depth = 0 으로 잡힘

for doc in docs:
    summarize(doc)                       # ← 실제로는 루프 안
```

실제 코드는 거의 이렇게 생겼습니다. API 호출을 함수로 감싸지 않는 코드가 오히려
드물죠. **지금 APrIce는 이 케이스를 전부 놓칩니다.** 이걸 잡으면 "루프 감지"라는
차별점이 데모용에서 실전용이 됩니다.

**할 일.** 파일 단위 호출 그래프를 만듭니다.

1. 모듈 안 `FunctionDef` 별로 "이 함수가 API를 호출하는가"를 표시
2. 루프 안에서 그런 함수를 부르는 지점을 찾음
3. `rules.py`에 `indirect-call-in-loop` 규칙 (severity `warn`).
   메시지에 **양쪽 위치를 모두** 표기: 호출 지점과 루프 지점

**범위 제한 — 넘지 마세요.**
- **같은 파일 안에서만** 추적합니다. 크로스 파일 호출 그래프는 8/29 안에 못 끝냅니다
- 재귀는 1단계만. 깊은 전이 추적 금지
- 확실하지 않으면 **경고를 내지 않습니다.** 오탐이 미탐보다 나쁩니다 —
  경고가 틀리기 시작하면 사용자가 도구 전체를 안 믿습니다

**완료 조건**
- [ ] 위 예시 코드에서 경고가 나옴
- [ ] 루프 밖에서만 호출되는 함수는 경고가 **안** 나옴
- [ ] 메시지에 호출 지점과 루프 지점이 둘 다 있음
- [ ] 오탐 테스트 포함 (경고가 나오면 안 되는 케이스)
- [ ] `pytest` · `ruff check .` 통과

> 이건 A 트랙에서 가장 어렵고 **발표에서 가장 잘 먹히는** 기능입니다.
> A-001~003을 먼저 끝내 안전지대를 확보한 뒤 시작하세요.

---

## A-005 — 탐지 정확도 테스트 픽스처 보강

**문제.** `tests/fixtures/sample_app.py` 하나로는 "정말 제대로 도는가"를 증명하기
부족합니다. 평가항목에 **"코드가 목적에 맞게 제 기능을 하는 정도"**가 명시돼
있고, 여기에 가장 싸게 답하는 게 테스트입니다.

**할 일.** 현실적인 픽스처를 추가하고 각각에 대한 기대 결과를 테스트로 고정합니다.

특히 **오탐이 나면 안 되는 케이스**를 반드시 넣으세요.

```python
# 주석 안의 client.messages.create(...) — 잡히면 안 됨
s = "client.messages.create()"          # 문자열 안 — 잡히면 안 됨
def messages(): ...                     # 이름만 같음 — 잡히면 안 됨
db.chat.completions.create()            # 우리 SDK 아님 (오탐 허용 여부 판단 필요)
```

앞의 셋은 정규식 기반 도구가 반드시 틀리는 지점입니다. **"우리는 AST라서 안
틀린다"를 테스트로 증명해두면 결과보고서에 그대로 인용할 수 있습니다.**

추가할 픽스처: async/await 호출 · `with ... stream()` · 중첩 루프 ·
컴프리헨션 안 호출 · 파싱 불가능한 깨진 파일(건너뛰는지 확인)

**완료 조건**
- [ ] 오탐 방지 케이스 최소 3개
- [ ] async · 스트리밍 · 중첩 루프 각각 커버
- [ ] 깨진 파일이 있어도 스캔 전체가 죽지 않음을 테스트로 확인
- [ ] `pytest` · `ruff check .` 통과
