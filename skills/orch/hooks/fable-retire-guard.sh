#!/bin/bash
# 降板ガード(Fable Orchestra 型 v0.4.3)
# 降板フラグ(docs/orchestration/.fable-retired)があるプロジェクトで、
# 従量モデル(Fable/Mythos)のままプロンプトを送るとブロックして切替を促す。
# フラグ無し・モデルがOpus等ならそのまま通す(exit 0)。

input=$(cat)

read -r cwd sid tp <<EOF
$(printf '%s' "$input" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('cwd',''), d.get('session_id',''), d.get('transcript_path',''))
" 2>/dev/null)
EOF

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
    {
      echo "⛔ 降板済みプロジェクトです(フラグ: $flag)"
      echo "現在の指令塔は従量モデル($model)。このまま続けると1往復ごとに課金されます。"
      echo "→ /model opus に切り替えてから、同じメッセージを再送してください。"
      echo "→ 意図的にFableを再登板する場合はフラグを削除: rm '$flag'"
    } >&2
    exit 2
    ;;
esac
exit 0
