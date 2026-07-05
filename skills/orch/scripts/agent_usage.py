#!/usr/bin/env python3
"""委譲先サブエージェントのトークン実測ツール(orch型 v0.4)。

Agentツールのoutput_file(JSONLトランスクリプト)からusageを合算し、
入力(非キャッシュ/キャッシュ書込/キャッシュ読取)と出力を分けて表示する。
「委譲先のインプットが見えないまま判断しない」ための実測ツール。

使い方:
  python3 agent_usage.py <transcript.jsonl> [<transcript2.jsonl> ...]
  複数渡すと合算行も出す。トランスクリプト本文はコンテキストに載せず数値のみ集計する。
"""
import json
import sys


def summarize(path: str) -> dict:
    totals = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage or msg.get("role") != "assistant":
                continue
            # 同一メッセージIDの継続行(コンテンツブロック分割)は同じusageが重複するため、
            # output_tokensが確定している行(stop_reasonあり or 最後の分割)だけ数えると
            # 二重計上する/しないの判定が難しい。ここでは message.id 単位で最後のusageを採用する。
            totals.setdefault("_by_id", {})[msg.get("id")] = usage
    by_id = totals.pop("_by_id", {})
    for usage in by_id.values():
        totals["input"] += usage.get("input_tokens", 0) or 0
        totals["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
        totals["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        totals["output"] += usage.get("output_tokens", 0) or 0
        totals["calls"] += 1
    totals["input_total"] = totals["input"] + totals["cache_write"] + totals["cache_read"]
    return totals


def fmt(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    grand = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0, "input_total": 0}
    print(f"{'transcript':<28} {'calls':>5} {'入力計':>8} {'(非ｷｬｯｼｭ':>9} {'+書込':>8} {'+読取)':>9} {'出力':>8}")
    for path in sys.argv[1:]:
        t = summarize(path)
        name = path.rsplit("/", 1)[-1][:26]
        print(f"{name:<28} {t['calls']:>5} {fmt(t['input_total']):>8} {fmt(t['input']):>9} {fmt(t['cache_write']):>8} {fmt(t['cache_read']):>9} {fmt(t['output']):>8}")
        for k in grand:
            grand[k] += t[k]
    if len(sys.argv) > 2:
        print(f"{'== 合算 ==':<28} {grand['calls']:>5} {fmt(grand['input_total']):>8} {fmt(grand['input']):>9} {fmt(grand['cache_write']):>8} {fmt(grand['cache_read']):>9} {fmt(grand['output']):>8}")


if __name__ == "__main__":
    main()
