# CS Harness · CS Studio 통합 설계 (초안 v0.1, 2026-11-27)

표시 규칙: **[검증]** 이 PC에서 실제로 돌려 확인함 · **[부분]** 일부만 확인 · **[미구현]** 설계만 있음

---

## 1. 전체 프로젝트 구조

```
cs-harness/
├─ CLAUDE.md                 교육 철학 + Claude 행동 원칙 + 이 폴더의 실행 규칙
├─ .claude/
│  ├─ settings.json          Hook 등록(모든 도구 호출이 harness/hook.py 를 거침)
│  └─ commands/시작.md        /시작
├─ harness/                  CS Harness (오케스트레이션)
│  ├─ start.py               진입점: Studio 켜기 → 브라우저 → claude /시작
│  ├─ hook.py                실행 제어: 허용/거부/대기/지연 + 이벤트 발행
│  └─ say.py                 Claude 설명을 Studio 에 올림
├─ studio/                   CS Studio (시연 화면)
│  ├─ server.py              이벤트 저장·SSE·상태·파일 조회 (Python 표준 라이브러리만)
│  └─ static/index.html      화면(탐색기·코드·터미널·설명·타임라인·제어)
├─ workspace/                실습 작업 공간. 독자의 서비스가 여기서 0 부터 자란다
├─ scenarios/                [미구현] 저자가 정제한 개발 시나리오
├─ tests/                    기능별 검증 스크립트
└─ .harness/                 실행 상태(포트, state.json, events.jsonl) — git 에 안 올림
```

세 영역은 논리 구분이다. 지금은 프로세스가 둘뿐이다: **Claude Code**(Q&A·판단·작업 요청) 와 **Studio 서버**(상태·이벤트·화면). 실행 엔진은 따로 만들지 않고 Claude Code 의 실제 도구(Write/Edit/Bash/PowerShell)를 그대로 쓰되, 모든 호출이 hook 을 지나가게 했다.

## 2. 하네스와 전용 도구의 실행 관계

```
독자 ── python harness/start.py
          │ ① Studio 서버가 꺼져 있으면 켬(빈 포트를 OS 에서 받아 .harness/port.txt 에 기록)
          │ ② 브라우저로 Studio 화면 열기
          └ ③ 같은 터미널에서 claude "/시작"
```

- **[검증]** 서버는 포트를 독점으로 잡는다. 이 PC 에서 8765·8791 을 이미 다른 프로그램이 쓰고 있었고, Python 기본 설정(`allow_reuse_address`)은 Windows 에서 남의 포트에 같이 붙어 버렸다 → 독점 바인드 + OS 배정 포트 + `app: cs-studio` 자기 확인으로 바꿈.
- **[부분]** `start.py` 의 서버 기동 경로는 시험했고, 브라우저 열기와 `claude /시작` 연결은 사용자 화면에 창이 떠서 아직 돌려 보지 않았다.

## 3. Claude 연동 방식

후보 셋을 비교했다.

| 방식 | 제어력 | 대화(Q&A) | 판단 |
|---|---|---|---|
| A. 대화형 Claude Code + **Hooks** | 도구 호출마다 허용/거부/대기 가능 | 터미널에서 그대로 | **채택(1차)** |
| B. `claude -p` / Agent SDK 를 하네스가 구동 | 턴 단위까지 완전 제어 | Studio 안 채팅창으로 옮겨야 함 | 2차 후보 |
| C. 프롬프트(CLAUDE.md)만 | 보장 없음 | 그대로 | 단독 사용 불가 |

A 를 고른 이유: 지금 필요한 "작업 전에 붙잡기·밖에 못 쓰게·출력 수집" 이 hook 만으로 됐고, 독자는 Claude Code 만 있으면 된다. Studio 화면 안에서 질문하게 하려면 B 로 넘어가야 하며, 그때 이벤트 구조는 그대로 쓴다.

## 4. 실제 개발 작업의 실행 경로

```
Claude 가 도구 호출 ─▶ PreToolUse hook ─▶ Studio /state 조회
                         │ Studio 꺼짐        → 거부
                         │ workspace 밖 파일   → 거부 + BLOCKED 이벤트
                         │ 하위 에이전트·기타   → 거부
                         │ 일시정지 중         → WAITING 이벤트, 풀릴 때까지 대기
                         │ 통과               → CREATE_FILE/MODIFY_CODE/RUN_COMMAND 이벤트 + 지연
                         ▼
                   실제 도구 실행(Claude Code)
                         ▼
                   PostToolUse hook ─▶ 실제 결과 확인(파일 존재·크기 / stdout·stderr) → VALIDATE·COMMAND_OUTPUT
```

- **[검증]** `tests/check_gate.py` 16/16, 실제 `claude -p` 로 돌린 `tests/check_real_claude.py` 7/7.
  - 실제 Claude 가 `workspace/real_hello.py` 를 만들고 실행했으며, 프로젝트 밖 `outside_test.txt` 는 hook 에 막혀 생기지 않았다. Claude 도 "막혔다" 고 그대로 보고했다.
  - 파일 생성 직후 일시정지를 걸자, 다음 Bash 는 WAITING 에 걸려 6초 동안 시작되지 않았고 재개 뒤 진행됐다.

## 5. 개발 이벤트 전달 방식

- 형식: `{id, ts, type, step, status, data}` 한 줄 JSON. `.harness/events.jsonl` 에 덧붙이고, 화면에는 SSE(`/stream`)로 흘린다. 새로 연결하면 지난 이벤트부터 다시 받는다.
- 지금 쓰는 종류: `STUDIO_STARTED STEP_STARTED CREATE_FILE WRITE_CODE MODIFY_CODE READ_FILE RUN_COMMAND COMMAND_OUTPUT VALIDATE EXPLAIN PAUSE RESUME WAITING BLOCKED`
- **[검증]** SSE 로 실제 이벤트가 화면 쪽에 전달됨(서버 응답 확인).
- 완료 판정은 실제 결과로만: 파일 이벤트는 PostToolUse 에서 파일이 실제로 있는지 보고 `VALIDATE ok/error` 를 붙인다 **[검증]**.
- **[미구현]** `INSTALL_PACKAGE START_PROCESS STOP_PROCESS HTTP_* DB_* CHECKPOINT STEP_COMPLETED` — 시나리오에서 처음 필요해질 때 만든다.

## 6. 프로젝트 파일과 코드의 실시간 표시

- 탐색기: `/tree` 가 `workspace/` 실제 디렉터리를 읽는다. 새로 생긴 파일은 몇 초간 강조.
- 코드: `VALIDATE ok` 가 오면 그 파일을 실제 디스크에서 다시 읽어 보여 주고, 직전에 본 내용에 없던 줄을 강조한다. 백그라운드 완성본을 마지막에 보여 주는 게 아니라, Claude 가 블록 하나를 쓸 때마다 화면이 바뀐다.
- **[검증]** `/tree`, `/file` 이 실제 파일을 돌려주고 `../CLAUDE.md` 같은 밖 경로는 거부.
- **[미구현]** 브라우저 화면이 실제로 제대로 그려지는지 눈으로 본 확인, 변경 전후 비교 화면, 사용자 직접 수정, 이전 상태 열람.
- 제약: 사용자가 에디터 등으로 직접 바꾼 파일은 이벤트가 없어 화면이 늦게 바뀐다 → 파일 감시(폴링/`watchdog`) 추가 필요.

## 7. 로그 수집 및 표시

- 원본: PostToolUse 가 받은 stdout/stderr 를 손대지 않고 기록 **[검증]**. 해설은 `EXPLAIN` 으로 따로 → 원본과 해설이 섞이지 않는다.
- 제약 **[확인됨]**: Claude Code 가 hook 에 주는 Bash 결과에는 **종료 코드가 없다**(`exit_code: null`). 종료 코드가 꼭 필요하면 ① 명령 끝에 종료 코드를 찍게 하거나 ② 하네스가 명령을 직접 실행(방식 B)해야 한다.
- 제약: 출력이 **끝난 뒤** 한 번에 온다. 서버 로그처럼 계속 나오는 출력은 hook 으로 실시간 표시가 안 된다 → 장시간 프로세스는 하네스 쪽 실행기(`START_PROCESS` + 파이프 수집)로 돌려야 한다 **[미구현]**.

## 8. 사용자 입력 감지 및 일시정지

- Studio 의 일시정지 버튼 → 상태 `paused` → 다음 도구 호출이 hook 에서 대기 **[검증]**.
- 운영체제 수준 감지: `server.py --watch-input` 은 `GetLastInputInfo` 로 **마지막 입력 시각만** 본다(키 내용은 읽지 않음). 재개 직후 3초는 무시해 재개 클릭으로 다시 멈추지 않게 했다. **[미구현 검증]** 실제 입력으로 시험하지 않음.
- 멈추는 지점: "다음 도구 호출 시작 전". 이미 실행 중인 쓰기·설치는 끊지 않는다(지침의 안전 정지).
- 제약:
  - Claude 가 **응답 글을 쓰는 동안**은 hook 이 없어 멈출 수 없다. 멈추는 단위는 도구 호출이다. 터미널에서 Esc 로 끊는 것은 Claude Code 기본 기능.
  - OS 감지는 사용자가 **터미널에 질문을 치는 것**도 입력으로 잡는다 → 질문 중 재개 금지와 맞아떨어지지만, 다른 프로그램을 쓰는 것과 구분하지 못한다.
  - 독자가 일시정지 중 터미널로 질문하면 Claude 는 대답(읽기 도구는 통과)할 수 있고, 쓰기·명령은 재개 전까지 대기한다.

## 9. 질의응답과 기존 개발 복귀

- 지금: 질문은 터미널의 Claude Code 에 한다. Claude 는 읽기 도구(Read/Glob/Grep)로 **실제 파일**을 보고 답한다 — 일시정지 중에도 가능 **[검증: 읽기 도구 통과]**.
- 실험이 필요한 질문(프로세스·포트·HTTP 확인 등)은 명령 실행이라 재개해야 한다. 과외 중 실험을 허용하려면 상태 `TUTORING/EXPLORING` 과 실험용 폴더(`workspace/_lab/`)를 두고, 그 상태에서는 시나리오 단계를 진행하지 않게 해야 한다 **[미구현]**.
- 복귀: 시나리오 단계 ID 와 마지막 `VALIDATE` 위치를 `state.json` 에 두고 `/시작` 이 거기서 이어 간다 **[미구현]**.

## 10. 저자 모드와 독자 모드

| | 저자 모드 | 독자 모드 |
|---|---|---|
| 목적 | 서비스를 처음부터 만들며 경로 발견 | 정제된 경로를 독자 PC 에서 실제로 실행 |
| 기록 | events.jsonl 전부 + 저자 메모(판단 근거, 실패와 해결) | 같은 형식으로 기록(복귀·재개용) |
| 산출물 | `scenarios/<id>.yaml` 초안 | — |
| 실행 | 자유 개발 | 시나리오 단계 순서, 단계마다 성공 조건 확인 |

- 지금 만든 것(hook + 이벤트 로그)이 저자 모드의 기록 장치가 된다. 저자가 하네스로 첫 서비스를 만들면 그 로그를 보고 시나리오 형식을 정한다. 시나리오 형식은 **미리 정하지 않는다**.

## 11. 최소 기능 프로토타입 — 현재 상태

| 지침 19 항목 | 상태 |
|---|---|
| 하네스 시작 및 전용 도구 자동 실행 | 부분(서버 기동 확인, 브라우저·claude 연결 미확인) |
| 실제 프로젝트 파일 탐색기 | 서버 검증 / 화면 미확인 |
| 소스코드 작성 및 변경 표시 | 서버 검증 / 화면 미확인 |
| 실시간 명령 실행 로그 | 명령 단위 검증(끝난 뒤 표시, 종료 코드 없음) |
| 개발 설명 영역 | say.py → SSE 검증 |
| 사용자 질의응답 | 터미널 Claude Code 그대로 |
| 사용자 입력 감지 | 버튼 검증 / OS 감지 코드만 |
| 자동 개발 일시정지 | 실제 Claude 로 검증 |
| 기존 개발 과정 재개 | 같은 세션 안에서 검증 / 세션 재시작 후 이어가기 미구현 |
| 현재 개발 위치 관리 | `/step` 엔드포인트만, 시나리오 없음 |

## 12. 기능별 검증 방법

| 기능 | 방법 |
|---|---|
| 실행 제어(거부·대기·지연·완료 판정) | `python -X utf8 tests/check_gate.py` — 시험용 임시 폴더·빈 포트로 Studio 를 따로 띄워 hook 에 가짜 입력 |
| 실제 Claude 경유 | `python -X utf8 tests/check_real_claude.py` — 실제 `claude -p` 가 작업 공간 안/밖 작업, 도중 일시정지 |
| 화면 | (다음) 저자가 `python harness/start.py` 로 띄워 눈으로 확인 — 탐색기·코드 강조·터미널·설명·타임라인이 이벤트와 맞는지 |
| 로그 일치 | events.jsonl 의 경로·출력과 실제 파일·명령 결과 대조(실제 Claude 시험에 포함) |
| OS 입력 감지 | (다음) `--watch-input` 켜고 마우스를 움직였을 때 PAUSE 이벤트가 찍히는지 |

## 13. 확인된 기술 제약과 해결 방안

1. **Bash/PowerShell 로 작업 공간 밖에 쓰는 것은 hook 이 막지 못한다.** 명령 문자열을 해석해서 막는 건 믿을 수 없다. → Claude Code 의 샌드박스 설정(쓰기 경로 제한) 적용 가능성을 다음에 시험. 그 전까지는 CLAUDE.md 규칙 + 명령 전부를 화면·로그에 남기는 것으로 감시.
2. **Bash 결과에 종료 코드가 없다.** → 7절.
3. **명령 출력이 실시간으로 오지 않는다.** → 7절, 장시간 프로세스는 하네스 실행기로.
4. **응답 생성 중에는 멈출 수 없다.** → 멈춤 단위는 도구 호출. 글은 짧게, 블록 하나 쓰고 설명하는 리듬을 CLAUDE.md 로 유도.
5. **이 PC 는 로컬 포트 충돌이 잦다.** → OS 배정 포트 + 독점 바인드 + 자기 확인(해결함).
6. **Windows 도구의 한글 인코딩(cp949)이 섞인다.** → 서버가 둘 다 받게 함(해결함).
7. **Studio 가 꺼지면 쓰기·명령이 전부 막힌다**(의도). 읽기는 열어 두어 질문에는 답할 수 있다.
