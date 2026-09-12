#!/usr/bin/env python3
"""トークン実測+枠負荷($換算)ツール(orch型 v1.1、2026-09-12 サブスク転換対応)。

3モード:
  (1) 委譲先実測:   agent_usage.py <transcript.jsonl> [<transcript2.jsonl> ...]
      AgentツールのJSONLトランスクリプトからusageを集計。
  (2) 指令塔実測:   agent_usage.py --main <session.jsonl> [--metered] [--weekly-usd N]
      メインセッションのJSONLをモデル別に集計し、枠負荷($換算)を出す。
      セッションJSONLの場所: ~/.claude/projects/<cwdの/を-に置換>/ の最新 *.jsonl
      (例: ls -t ~/.claude/projects/<cwdを-区切りにした名前>/*.jsonl | head -1)
  (3) 事前見積り:   agent_usage.py --estimate <Fableリクエスト数> [--ctx 145] [--cold 1] [--out 1.4]
      着手前にFable指令塔の枠負荷($換算)レンジを出す(GO/NO-GO提示用)。--ctx/--outはk tok単位。

v1.1の単位について:
- Maxプランでは Fable も週次枠の内側(50%上限・Opus比約2倍の重み)。金額は請求されないが、
  枠の重みは単価に比例する、を作業仮説として USD換算値 = 「枠負荷」の指標に使う。
- --metered: Fableの50%枠到達後にクレジット継続を承認した区間(=本当に従量)の実額表示に切り替える。
- --weekly-usd N: 「週次枠100% = N ドル換算」の較正値。与えると 週次枠% と Fable枠(50%)% も表示する。
  較正のとり方: セッション前後の /usage 差分(%) と本ツールの$換算を3〜5標本並べて N を決める。

集計の注意(2026-07-05 実測で確定):
- 同一message.idの行が複数ある(ストリーミング分割)ため、id単位で各usageフィールドのmaxを取る。
  出力トークンは累積更新されるので、初出値や単純合算では大幅に狂う(実測: 87.6k→7.8kに過小)。
- 単価(USD/MTok、API公表値): Fable $10/$50(キャッシュ読取$0.25=Fable 5.1で改定)、Opus $5/$25、
  Sonnet $3/$15、Haiku $1/$5。キャッシュ書込=入力単価×1.25(5分TTL)。読取は家族ごとの単価。
  1時間TTL書込は×2(要ダッシュボード照合)。
"""
import argparse
import json
import sys

# USD per MTok: (input, output, cache_read)。書込=input×1.25
PRICES = {
    "fable": (10.0, 50.0, 0.25),
    "opus": (5.0, 25.0, 0.50),
    "sonnet": (3.0, 15.0, 0.30),
    "haiku": (1.0, 5.0, 0.10),
}
TOP_FAMILIES = {"fable"}  # 週次枠の50%上限がかかる「重い」モデル(=指令塔候補)
FABLE_SHARE = 0.5         # Fable枠 = 週次枠の50%


def model_family(model: str) -> str:
    m = (model or "").lower()
    for fam in PRICES:
        if fam in m:
            return fam
    return "opus"  # 不明時は保守的にOpus単価


def usd(family: str, t: dict) -> float:
    pin, pout, pread = PRICES[family]
    return (
        t["input"] * pin
        + t["cache_write"] * pin * 1.25
        + t["cache_read"] * pread
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


def print_row(name, t, family=None, metered=False):
    cost = ""
    if family:
        if family in TOP_FAMILIES:
            tag = "(従量)" if metered else "(Fable枠)"
        else:
            tag = "(共通枠)"
        cost = f" ${usd(family, t):>7.2f}{tag}"
    total = t["input"] + t["cache_write"] + t["cache_read"]
    print(f"{name:<30} {t['calls']:>5} {fmt(total):>9} {fmt(t['input']):>9} "
          f"{fmt(t['cache_write']):>8} {fmt(t['cache_read']):>9} {fmt(t['output']):>8}{cost}")


def header(with_cost=False):
    cost = f" {'$換算':>8}" if with_cost else ""
    print(f"{'対象':<30} {'calls':>5} {'入力計':>8} {'非ｷｬｯｼｭ':>7} {'書込':>7} {'読取':>8} {'出力':>7}{cost}")


def quota_lines(top_usd: float, all_usd: float, weekly_usd: float):
    if not weekly_usd:
        return
    print(f"週次枠換算(較正値: 100%={weekly_usd:.0f}$換算): "
          f"Fable枠 {top_usd / (weekly_usd * FABLE_SHARE) * 100:.1f}% (50%枠の内訳) / "
          f"週次枠全体 {all_usd / weekly_usd * 100:.1f}%(全モデル)")


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


def mode_main(path, metered=False, weekly_usd=None):
    by_model = collect(path)
    header(with_cost=True)
    top_total = 0.0
    all_total = 0.0
    for model in sorted(by_model):
        t = by_model[model]
        fam = model_family(model)
        print_row(model[:28], t, family=fam, metered=metered)
        c = usd(fam, t)
        all_total += c
        if fam in TOP_FAMILIES:
            top_total += c
    label = "Fable従量(実額)合計" if metered else "Fable枠負荷($換算)合計"
    print(f"\n{label}: ${top_total:.2f}   / 全モデル$換算: ${all_total:.2f}")
    quota_lines(top_total, all_total, weekly_usd)
    print("※書込は5分TTL(×1.25)換算。Fableのキャッシュ読取は$0.25/MTok(5.1改定)。"
          "枠の重みは単価比例が作業仮説 — /usage の前後差分と照合して較正する。")


def mode_estimate(requests, ctx_k, cold, out_k, weekly_usd=None):
    pin, pout, pread = PRICES["fable"]
    per_req = ctx_k * 1000 * pread / 1e6 + out_k * 1000 * pout / 1e6
    cold_cost = ctx_k * 1000 * pin * 1.25 / 1e6
    point = requests * per_req + cold * cold_cost
    lo, hi = point * 0.7, point * 1.4
    print(f"Fable指令塔 事前見積り — 枠負荷($換算。較正前 確度±30〜50%)")
    print(f"  前提: リクエスト{requests}回 × (文脈{ctx_k:.0f}k読取 + 出力{out_k:.1f}k) "
          f"+ キャッシュ切れ{cold}回")
    print(f"  1リクエストあたり ≈ ${per_req:.2f} / キャッシュ切れ1回 ≈ ${cold_cost:.2f}")
    print(f"  → 概算 ${point:.1f}(レンジ ${lo:.1f}〜${hi:.1f})")
    if weekly_usd:
        print(f"  → Fable枠(週次50%)の {point / (weekly_usd * FABLE_SHARE) * 100:.1f}% 相当"
              f"(較正値 100%={weekly_usd:.0f}$換算)")
    print(f"  リクエスト数の目安: 計画・委譲指示5〜8 + WPあたり検収3〜5 + 差し戻し1回3〜5")
    print(f"  着手前に /usage のFable残%を確認し、逼迫なら(b)構成か翌週送りを提案する(skill §3)")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--main", dest="main_path")
    ap.add_argument("--estimate", type=int)
    ap.add_argument("--ctx", type=float, default=145.0, help="平均文脈 k tok(既定145=実測例)")
    ap.add_argument("--cold", type=int, default=1, help="キャッシュ切れ想定回数")
    ap.add_argument("--out", type=float, default=1.4, help="1リクエスト平均出力 k tok")
    ap.add_argument("--metered", action="store_true",
                    help="Fable行を従量(実額)扱いで表示(50%枠到達後にクレジット継続を承認した区間用)")
    ap.add_argument("--weekly-usd", type=float, default=None,
                    help="較正値: 週次枠100%%のドル換算。与えると枠%%も表示")
    args = ap.parse_args()

    if args.estimate:
        mode_estimate(args.estimate, args.ctx, args.cold, args.out, args.weekly_usd)
    elif args.main_path:
        mode_main(args.main_path, metered=args.metered, weekly_usd=args.weekly_usd)
    elif args.paths:
        mode_subagents(args.paths)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
