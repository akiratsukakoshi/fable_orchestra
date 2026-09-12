#!/bin/bash
# 降板ガード(Fable Orchestra 型 v0.4.3 → v1.1で「警告のみ」に変更 2026-09-12)
# 降板フラグ(docs/orchestration/.fable-retired)があるプロジェクトで、
# 最上位モデル(Fable/Mythos)のままプロンプトを送ると警告を表示する(ブロックはしない)。
# 目的は課金防止ではなく「派生対話でFable枠(週次50%上限)を溶かしている」ことの可視化。
# フラグ無し・モデルがOpus等・判定不能なら何も出さず通す(exit 0)。

input=$(cat)

read -r cwd sid tp <<EOF2
$(printf '%s' "$input" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('cwd',''), d.get('session_id',''), d.get('transcript_path',''))
" 2>/dev/null)
EOF2

[ -n "$cwd" ] || exit 0
flag="$cwd/docs/orchestration/.fable-retired"
[ -f "$flag" ] || exit 0

# トランスクリプトの所在(hook入力に無ければ規約パスから解決)
if [ ! -f "$tp" ]; then
  slug=$(printf '%s' "$cwd" | sed 's|/|-|g')
  tp="$HOME/.claude/projects/$slug/$sid.jsonl"
fi
[ -f "$tp" ] || exit 0

# 直近のassistantメッセージのモデルを判定(末尾500行で十分)
model=$(tail -n 500 "$tp" | python3 -c "
import sys, json
m = ''
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    msg = d.get('message')
    if isinstance(msg, dict) and msg.get('model'):
        m = msg['model']
print(m)
" 2>/dev/null)

case "$model" in
  *fable*|*mythos*)
    python3 - "$model" "$flag" <<'PY'
import json, sys
model, flag = sys.argv[1], sys.argv[2]
msg = (f"⚠ Fable枠を使っています: このプロジェクトは降板済み(フラグ: {flag})ですが、"
       f"指令塔は {model} のままです。派生対話なら /model opus への切替を検討してください"
       f"(Fableは週次枠の50%上限・Opus比約2倍の重み)。意図的な再登板ならフラグを削除: rm '{flag}'")
print(json.dumps({
    "systemMessage": msg,
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "[降板ガード] このプロジェクトは降板済み(.fable-retired あり)だが指令塔がFableのまま。"
                             "返答の冒頭で一言、/model opus への切替(または再登板ならフラグ削除)を提案すること。ブロックはしない。"
    }
}, ensure_ascii=False))
PY
    ;;
esac
exit 0
