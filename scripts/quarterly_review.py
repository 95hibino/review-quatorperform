"""四半期レビュー: ウォッチリストの還元スコアを一括再計算し、前回との差分を出す。

決算シーズン(2月/5月/8月/11月 中旬)ごとに実行する想定。既存4スクリプトの
純粋関数を再利用し、ティッカーごとに yfinance を1回取得して全レイヤーを算出する:
  余地(最大30) + 実行(増配点+買い点) + 前向き(最大20) + 方針(最大6) = 統合スコア

出力:
  reports/data/quarterly/snapshot_YYYYMMDD.csv  … 当四半期スナップショット
  reports/YYYYMMDD_quarterly_review.md          … 前回比の差分レポート雛形(要Web確認リスト付き)

前回スナップショットが存在すれば、統合スコア/両輪/配当質の変化を差分表示する。
※ 方針スコアは finalize_tierA_ranking.POLICY(手動IR)を流用。四半期ごとに要人手確認。

実行:
  python scripts/quarterly_review.py [--limit N] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from compute_shareholder_return_capacity import (  # noqa: E402
    score_netcash_ratio, score_payout, score_de)
from detect_return_execution import (  # noqa: E402
    fiscal_year_dividends, dividend_quality, share_change_adjusted, buyback_class)
from rank_tierA_forward import (  # noqa: E402
    progressive_years, s_forecast, s_prog, s_growth, s_payout)
from finalize_tierA_ranking import POLICY  # noqa: E402

WATCHLIST = ROOT / "data" / "watchlist.csv"
SNAP_DIR = ROOT / "reports" / "data" / "quarterly"


def to_f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def analyze(code, name, gyou, now):
    t = yf.Ticker(code + ".T")
    try:
        info = t.info
    except Exception:
        info = {}
    cash, debt = to_f(info.get("totalCash")), to_f(info.get("totalDebt"))
    mcap = to_f(info.get("marketCap"))
    pbr = to_f(info.get("priceToBook"))
    roa = to_f(info.get("returnOnAssets"))
    payout = to_f(info.get("payoutRatio"))
    de = to_f(info.get("debtToEquity"))
    fwd = to_f(info.get("dividendRate"))
    growth = to_f(info.get("earningsGrowth"))

    nc_ratio = ((cash - debt) / mcap) if (cash is not None and debt is not None and mcap) else None
    yochi = score_netcash_ratio(nc_ratio) + score_payout(payout) + score_de(de)

    fy = fiscal_year_dividends(t)
    yoy, dlabel, zoshi, dscore = dividend_quality(fy)
    adj, split_flag, split_note = share_change_adjusted(t)
    blabel, bscore = buyback_class(adj, split_flag)
    both = dscore >= 2 and bscore >= 1

    latest_fy = float(fy.iloc[-1]) if fy is not None and len(fy) else None
    fcast = ((fwd / latest_fy - 1) * 100) if (fwd and latest_fy) else None
    prog = progressive_years(fy) if fy is not None and len(fy) else 0
    fwd_score = (s_forecast(fcast)[0] + s_prog(prog)
                 + s_growth(growth)[0] + s_payout(payout))

    pol, star, pnote = POLICY.get(code, (0, "-", "未確認"))
    total = yochi + dscore + bscore + fwd_score + pol

    flags = []
    if dlabel == "減配":
        flags.append("減配")
    if dlabel == "ピーク割れ(質注意)":
        flags.append("ピーク割れ")
    if split_note:
        flags.append(f"分割({split_note})")
    if nc_ratio is None or payout is None:
        flags.append("データ欠損")

    return {
        "コード": code, "銘柄": name, "業種": gyou,
        "PBR": round(pbr, 2) if pbr else "", "ROA%": round(roa * 100, 1) if roa is not None else "",
        "余地": yochi, "配当質": dlabel, "増配率%": round(yoy, 1) if yoy is not None else "",
        "両輪": "○" if both else "", "自社株買い": blabel,
        "前向き": fwd_score, "会社予想増配%": round(fcast, 1) if fcast is not None else "",
        "累進年数": prog, "方針": star, "方針点": pol,
        "統合スコア": total, "フラグ": "/".join(flags),
    }


def latest_prior_snapshot(today):
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(SNAP_DIR.glob("snapshot_*.csv"))
    snaps = [s for s in snaps if s.stem.split("_")[1] < today.strftime("%Y%m%d")]
    return snaps[-1] if snaps else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    today = date.fromisoformat(args.date)
    now = pd.Timestamp(today)

    wl = list(csv.DictReader(WATCHLIST.open(encoding="utf-8-sig")))
    if args.limit:
        wl = wl[:args.limit]
    print(f"四半期レビュー {today} / ウォッチリスト {len(wl)}社")

    # detect_return_execution / rank_tierA_forward は NOW を固定値で持つため基準日を上書き
    import detect_return_execution as dre
    import rank_tierA_forward as rtf
    dre.NOW = now
    rtf.NOW = now

    rows = []
    for i, r in enumerate(wl):
        rows.append(analyze(r["コード"], r["銘柄"], r["業種"], now))
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(wl)}")
    rows.sort(key=lambda x: -x["統合スコア"])

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAP_DIR / f"snapshot_{today.strftime('%Y%m%d')}.csv"
    with snap.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"スナップショット: {snap}")

    # 差分
    prior = latest_prior_snapshot(today)
    diffs, warnings = [], []
    prior_map = {}
    if prior:
        for r in csv.DictReader(prior.open(encoding="utf-8-sig")):
            prior_map[r["コード"]] = r
    for r in rows:
        p = prior_map.get(r["コード"])
        if not p:
            diffs.append(f"【新規】{r['銘柄']} 統合{r['統合スコア']} {r['配当質']}")
            continue
        d_score = r["統合スコア"] - int(p["統合スコア"])
        if abs(d_score) >= 3:
            diffs.append(f"{r['銘柄']}: 統合 {p['統合スコア']}→{r['統合スコア']} ({d_score:+d}) "
                         f"配当質[{p['配当質']}→{r['配当質']}] 両輪[{p['両輪'] or '×'}→{r['両輪'] or '×'}]")
        if r["配当質"] == "減配" and p["配当質"] != "減配":
            warnings.append(f"⚠️減配転落: {r['銘柄']}")
        if p["両輪"] == "○" and r["両輪"] != "○":
            warnings.append(f"⚠️両輪外れ: {r['銘柄']}（{r['配当質']}/{r['自社株買い']}）")

    # 要Web確認: 上位・大変動・警報銘柄
    to_check = [r for r in rows[:15]] + \
               [r for r in rows if r["フラグ"] and r not in rows[:15]]

    report = ROOT / "reports" / f"{today.strftime('%Y%m%d')}_quarterly_review.md"
    with report.open("w", encoding="utf-8") as f:
        f.write(f"# 四半期レビュー {today}（ウォッチリスト{len(wl)}社）\n\n")
        f.write(f"- 前回スナップショット: {prior.name if prior else 'なし(初回)'}\n")
        f.write("- スクリプト: [scripts/quarterly_review.py](../scripts/quarterly_review.py)\n")
        f.write(f"- スナップショット: `reports/data/quarterly/{snap.name}`\n\n")
        f.write("> 本レポートは自動生成の雛形。CLAUDE.mdの鉄則に従い、下記「要Web確認」の各社について\n"
                "> **最新四半期決算・配当予想修正・自己株進捗・中計最終年度目標**をWebで確認し、\n"
                "> 解釈と最終序列をこの下に追記すること。\n\n")
        f.write("## 警報（減配・両輪外れ）\n\n")
        f.write(("\n".join(f"- {w}" for w in warnings) or "- なし") + "\n\n")
        f.write("## 前回比の主な変化（統合スコア±3以上）\n\n")
        f.write(("\n".join(f"- {d}" for d in diffs) or "- 重要な変化なし") + "\n\n")
        f.write("## 現在の統合ランキング 上位15\n\n")
        f.write("| 順 | 統合 | 方針 | コード | 銘柄 | PBR | 配当質 | 両輪 | 会社予想増配 | フラグ |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows[:15], 1):
            f.write(f"| {i} | {r['統合スコア']} | {r['方針']} | {r['コード']} | {r['銘柄']} | "
                    f"{r['PBR']} | {r['配当質']} | {r['両輪'] or ''} | "
                    f"{r['会社予想増配%'] if r['会社予想増配%']!='' else '-'}% | {r['フラグ']} |\n")
        f.write("\n## 要Web確認（この四半期に決算・修正を追うべき銘柄）\n\n")
        for r in to_check:
            f.write(f"- [{r['コード']}] {r['銘柄']}（統合{r['統合スコア']}／{r['配当質']}"
                    f"{'／'+r['フラグ'] if r['フラグ'] else ''}）\n")
    print(f"レポート雛形: {report}")
    if warnings:
        print("警報:", "; ".join(warnings))


if __name__ == "__main__":
    main()
