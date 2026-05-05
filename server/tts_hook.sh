#!/bin/bash
# Claude Code Stop hook — 최근 응답 완료 시 TTS 요약 재생
# stdin: JSON {"session_id":"...","transcript_path":"..."}

set -euo pipefail

TMPINPUT=$(mktemp)
cat > "$TMPINPUT"

python3 - "$TMPINPUT" << 'PYEOF'
import json, sys, subprocess, os, urllib.request, re

SERVER   = os.environ.get("VT_SERVER", "http://localhost:7777")
MAX_CHARS = 200
TMP_AUDIO = "/tmp/claude_tts.mp3"

input_file = sys.argv[1]
try:
    with open(input_file) as f:
        input_data = json.load(f)
finally:
    os.unlink(input_file)

transcript_path = input_data.get("transcript_path", "")
if not transcript_path or not os.path.isfile(transcript_path):
    sys.exit(0)

# ── 마지막 assistant 메시지 한 개만 추출 ──────────────────────────
last_assistant = None
with open(transcript_path, "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("role") == "assistant":
            last_assistant = obj   # 덮어쓰기 → 루프 끝에 최신 메시지만 남음

if not last_assistant:
    sys.exit(0)

# ── content에서 마지막 text 블록만 사용 ──────────────────────────
# tool_use / tool_result / thinking 블록 제외.
# 여러 text 블록이 있을 때 마지막 블록이 최종 답변.
content = last_assistant.get("content", "")
text = ""

if isinstance(content, list):
    text_blocks = [
        p.get("text", "").strip()
        for p in content
        if isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
    ]
    text = text_blocks[-1] if text_blocks else ""
elif isinstance(content, str):
    text = content.strip()

if not text:
    sys.exit(0)

# ── 길이 제한: 마지막 MAX_CHARS 문자 (결론은 끝부분에) ───────────
# 단, 문장 경계를 지키기 위해 첫 문장 경계까지 앞으로 밀어냄
if len(text) > MAX_CHARS:
    tail = text[-MAX_CHARS:]
    # 첫 불완전 문장 제거 (첫 '.', '!', '?', '\n' 이후부터 시작)
    m = re.search(r'[.!?\n]', tail)
    if m and m.start() < MAX_CHARS // 2:
        tail = tail[m.start() + 1:].lstrip()
    text = tail if tail else text[-MAX_CHARS:]

if not text:
    sys.exit(0)

# ── TTS: 서버 → macOS say fallback ───────────────────────────────
try:
    req = urllib.request.Request(
        f"{SERVER}/voice/output",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        with open(TMP_AUDIO, "wb") as f:
            f.write(resp.read())
    subprocess.Popen(["afplay", TMP_AUDIO])
except Exception:
    subprocess.Popen(["say", "-v", "Yuna", text])

# ── 모바일 푸시 (ntfy) ────────────────────────────────────────────
notify_url = os.environ.get("VT_NOTIFY_URL", "").strip()
if notify_url:
    try:
        push_req = urllib.request.Request(
            notify_url,
            data=text.encode("utf-8"),
            headers={
                "Title": "Claude 완료".encode("utf-8"),
                "Priority": "default",
                "Tags": "robot",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        urllib.request.urlopen(push_req, timeout=3)
    except Exception:
        pass
PYEOF
