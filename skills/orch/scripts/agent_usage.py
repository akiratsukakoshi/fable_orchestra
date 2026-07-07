#!/usr/bin/env python3
"""トークン実測+金額換算ツール(orch型 v0.4.2)。

3モード:
  (1) 委譲先実測:   agent_usage.py <transcript.jsonl> [<transcript2.jsonl> ...]
      AgentツールのJSONLトランスクリプトからusageを集計(従来機能)。
  (2) 指令塔実測:   agent_usage.py --main <session.jsonl>
      メインセッションのJSONLをモデル別に集計し、USD金額も出す。
      セッションJSONLの場所: ~/.claude/projects/<cwdの/を-に置換>/ の最新 *.jsonl
      (例: ls -t ~/.claude/projects/<cwdの/を-に置換>/*.jsonl | head -1)
  (3) 事前見積り:   agent_usage.py --estimate <Fableリクエスト数> [--ctx 145] [--cold 1] [--out 1.4]
      着手前にFable指令塔コストのUSDレンジを出す(GO/NO-GO提示用)。--ctx/--outはk tok単位。

集計の注意(2026-07-05 実測で確定):
- 同一message.idの行が複数ある(ストリーミング分割)ため、id単位で各usageフィールドのmaxを取る。
  出力トークンは累積更新されるので、初出値や単純合算では大幅に狂う(実測: 87.6k→7.8kに過小)。
- 単価(USD/MTok): Fable $10/$50、Opus $5/$25、Sonnet $3/$15、Haiku $1/$5。
  キャッシュ書込=入力単価×1.25(5分TTL)、読取=×0.1。1時間TTL書込は×2(要ダッシュボード照合)。
- 枠内(サブスク)モデルの行は「枠内」と表示し、USDは従量換算の参考値。
"""
import argparse
import json
import sys

# USD per MTok: (input, output)。書込=input×1.25、読取=input×0.1
PRICES = {
    "fable": (10.0, 50.0),
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
METERED_FAMILIES = {"fable"}  # このプロジェクトで従量請求されるモデル


def model_family(model: str) -> str:
    m = (model or "").lower()
    for fam in PRICES:
        if fam in m:
            return fam
    return "opus"  # 不明時は保守的にOpus単価


def usd(family: str, t: dict) -> float:
    pin, pout = PRICES[family]
    return (
        t["input"] * pin
        + t["cache_write"] * pin * 1.25
        + t["cache_read"] * pin * 0.10
        + t["output"] * pout
    ) / 1_000_000


def collect(path: str) -> dict:
    """message.id単位で各usageフィールドのmaxを取り、モデル別に合算して返す。"""
    per_id: dict = {}
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
            mid = msg.get("id") or rec.get("requestId")
            r = per_id.setdefault(mid, {"model": msg.get("model") or "?",
                                        "input": 0, "cache_write": 0, "cache_read": 0, "output": 0})
            r["input"] = max(r["input"], usage.get("input_tokens") or 0)
            r["cache_write"] = max(r["cache_write"], usage.get("cache_creation_input_tokens") or 0)
            r["cache_read"] = max(r["cache_read"], usage.get("cache_read_input_tokens") or 0)
            r["output"] = max(r["output"], usage.get("output_tokens") or 0)
    by_model: dict = {}
    for r in per_id.values():
        t = by_model.setdefault(r["model"], {"input": 0, "cache_write": 0, "cache_read": 0,
                                             "output": 0, "calls": 0})
        for k in ("input", "cache_write", "cache_read", "output"):
            t[k] += r[k]
        t["calls"] += 1
    return by_model


def flatten(by_model: dict) -> dict:
    t = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0}
    for m in by_model.values():
        for k in t:
            t[k] += m[k]
    t["input_total"] = t["input"] + t["cache_write"] + t["cache_read"]
    return t


def fmt(n) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(int(n))


def print_row(name, t, family=None):
    cost = ""
    if family:
        tag = "" if family in METERED_FAMILIES else "(枠内)"
        cost = f" ${usd(family, t):>7.2f}{tag}"
    total = t["input"] + t["cache_write"] + t["cache_read"]
    print(f"{name:<30} {t['calls']:>5} {fmt(total):>9} {fmt(t['input']):>9} "
          f"{fmt(t['cache_write']):>8} {fmt(t['cache_read']):>9} {fmt(t['output']):>8}{cost}")


def header(with_cost=False):
    cost = f" {'USD':>8}" if with_cost else ""
    print(f"{'対象':<30} {'calls':>5} {'入力計':>8} {'非ｷｬｯｼｭ':>7} {'書込':>7} {'読取':>8} {'出力':>7}{cost}")


def mode_subagents(paths):
    header()
    grand = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0}
    for path in paths:
        t = flatten(collect(path))
        print_row(path.rsplit("/", 1)[-1][:28], t)
        for k in grand:
            grand[k] += t[k]
    if len(paths) > 1:
        print_row("== 合算 ==", grand)


def mode_main(path):
    by_model = collect(path)
    header(with_cost=True)
    metered_total = 0.0
    for model in sorted(by_model):
        t = by_model[model]
        fam = model_family(model)
        print_row(model[:28], t, family=fam)
        if fam in METERED_FAMILIES:
            metered_total += usd(fam, t)
    print(f"\n従量(Fable)合計: ${metered_total:.2f}")
    print("※書込は5分TTL(×1.25)換算。1hTTLなら書込×2で再計算。ダッシュボード実測と照合して較正する。")


def mode_estimate(requests, ctx_k, cold, out_k):
    pin, pout = PRICES["fable"]
    per_req = ctx_k * 1000 * pin * 0.10 / 1e6 + out_k * 1000 * pout / 1e6
    cold_cost = ctx_k * 1000 * pin * 1.25 / 1e6
    point = requests * per_req + cold * cold_cost
    lo, hi = point * 0.7, point * 1.4
    print(f"Fable指令塔 事前見積り(較正前 確度±30〜50%)")
    print(f"  前提: リクエスト{requests}回 × (文脈{ctx_k:.0f}k読取 + 出力{out_k:.1f}k) "
          f"+ キャッシュ切れ{cold}回")
    print(f"  1リクエストあたり ≈ ${per_req:.2f} / キャッシュ切れ1回 ≈ ${cold_cost:.2f}")
    print(f"  → 概算 ${point:.1f}(レンジ ${lo:.1f}〜${hi:.1f})")
    print(f"  リクエスト数の目安: 計画・委譲指示5〜8 + WPあたり検収3〜5 + 差し戻し1回3〜5")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--main", dest="main_path")
    ap.add_argument("--estimate", type=int)
    ap.add_argument("--ctx", type=float, default=145.0, help="平均文脈 k tok(既定145=実測較正値)")
    ap.add_argument("--cold", type=int, default=1, help="キャッシュ切れ想定回数")
    ap.add_argument("--out", type=float, default=1.4, help="1リクエスト平均出力 k tok")
    args = ap.parse_args()

    if args.estimate:
        mode_estimate(args.estimate, args.ctx, args.cold, args.out)
    elif args.main_path:
        mode_main(args.main_path)
    elif args.paths:
        mode_subagents(args.paths)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
