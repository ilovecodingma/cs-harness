# cs-harness

개발 경험 없는 사람이 AI 가 서비스를 만드는 과정을 화면(CS Studio)으로 지켜보며, 궁금할 때 멈추고 묻는 교육용 하네스입니다.

## 시작

```
git clone https://github.com/ilovecodingma/cs-harness
cd cs-harness
python harness/start.py
```

CS Studio 프로그램 창이 열리고, 같은 터미널에서 Claude Code 가 `/시작` 으로 열립니다. Claude 가 쓰려는 코드는 실제로 쓰이기 전에 Studio 에 블록 하나씩 먼저 나타나고, 설명과 함께 보여 준 뒤에 저장됩니다.

필요한 것: Python 3.10 이상, Claude Code, Windows 는 WebView2(윈도우 11 기본 포함). `pywebview` 는 처음 시작할 때 자동 설치합니다.

- **Space** 또는 화면의 **일시정지** 를 누르면 다음 작업이 시작되지 않습니다. 그동안 터미널에서 자유롭게 질문하세요.
- **재개**(Space) 를 누르면 이어서 진행합니다. **→** 는 지금 블록을 건너뜁니다.

설계와 현재 구현 상태: [docs/DESIGN.md](docs/DESIGN.md)
