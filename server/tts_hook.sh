#!/bin/bash
# Claude Code Stop hook — 작업 완료 시 TTS 요약 재생
# stdin: JSON {"session_id":"...","transcript_path":"..."}

set -euo pipefail

# stdin을 임시 파일에 저장 후 Python에서 처리
TMPINPUT=$(mktemp)
cat > "$TMPINPUT"

python3 - "$TMPINPUT" << 'PYEOF'
import json, sys, subprocess, os, urllib.request

SERVER = os.environ.get("RALPH_SERVER", "http://localhost:7777")
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

# 마지막 assistant 응답 추출
text = ""
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
            content = obj.get("content", "")
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text"]
                text = " ".join(parts)
            elif isinstance(content, str):
                text = content

if not text.strip():
    sys.exit(0)

if len(text) > MAX_CHARS:
    text = text[:MAX_CHARS] + "..."

# TTS: 서버 → macOS say fallback
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

# 모바일 푸시 (ntfy) — RALPH_NOTIFY_URL 설정돼 있을 때만
notify_url = os.environ.get("RALPH_NOTIFY_URL", "").strip()
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
