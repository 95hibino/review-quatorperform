"""増配ポテンシャル本命群の四半期トラッキング。

watchlist_dividend_growth.csv の本命群について、四半期ごとに
  ・年間配当(直近12か月合計 DPS-TTM)  ・配当性向  ・利回り  ・PBR  ・株価
を取得してスナップショット化し、前回スナップショットとの差分で
「増配/据置/減配」「利回り変化」を検出する。
＝ 投資仮説（中計目標・累進/DOE方針）が“実際の増配”として実行されているかを追う。

出力:
  reports/data/dividend_growth_tracking/snapshot_YYYYMMDD.csv  … 当該時点スナップショット
  reports/YYYYMMDD_dividend_growth_tracking.md                 … 前回比の増配トラッキング

実行: python scripts/track_dividend_growth.py [--date YYYY-MM-DD]
想定タイミング: 決算シーズン(2/5/8/11月中旬)。既存 quarterly_review と同時運用可。
"""
from __future__ import annotations

import argparse
import csv
import glob
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "data" / "watchlist_dividend_growth.csv"
SNAP_DIR = ROOT / "reports" / "data" / "dividend_growth_tracking"


def to_f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def dps_ttm(t, asof):
    """直近12か月の配当合計(DPS-TTM)。"""
    try:
        divs = t.dividends
        if divs is None or len(divs) == 0:
            return None
        idx = divs.index.tz_localize(None) if divs.index.tz is not None else divs.index
        s = divs.copy(); s.index = idx
        cut = pd.Timestamp(asof) - pd.DateOffset(years=1)
        return round(float(s[(s.index > cut) & (s.index <= pd.Timestamp(asof))].sum()), 2)
    except Exception:
        return None


def analyze(code, asof):
    t = yf.Ticker(code + ".T")
    info = {}
    try:
        info = t.info
    except Exception:
        pass
    price = to_f(info.get("currentPrice")) or to_f(info.get("previousClose"))
    return {
        "DPS_TTM": dps_ttm(t, asof),
        "配当性向%": round(to_f(info.get("payoutRatio")) * 100, 1) if to_f(info.get("payoutRatio")) is not None else None,
        "利回り%": to_f(info.get("dividendYield")),
        "PBR": round(to_f(info.get("priceToBook")), 2) if to_f(info.get("priceToBook")) is not None else None,
        "株価": price,
    }


def latest_prev_snapshot(cur_path):
    files = sorted(glob.glob(str(SNAP_DIR / "snapshot_*.csv")))
    files = [f for f in files if Path(f) != cur_path]
    return files[-1] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    asof = args.date

    rows = list(csv.DictReader(WATCH.open(encoding="utf-8-sig")))
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    cur_path = SNAP_DIR / f"snapshot_{asof.replace('-','')}.csv"

    snap = []
    for r in rows:
        m = analyze(r["code"], asof)
        snap.append({**{k: r[k] for k in ("code", "銘柄", "グループ")}, **m})
        print(f"  {r['code']} {r['銘柄']:16} DPS-TTM={m['DPS_TTM']} 性向={m['配当性向%']}% 利={m['利回り%']}%")

    with cur_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(snap[0].keys()))
        w.writeheader(); w.writerows(snap)

    prev_path = latest_prev_snapshot(cur_path)
    prev = {}
    if prev_path:
        for r in csv.DictReader(Path(prev_path).open(encoding="utf-8-sig")):
            prev[r["code"]] = r

    # レポート
    rep = ROOT / "reports" / f"{asof.replace('-','')}_dividend_growth_tracking.md"
    lines = [f"# 増配ポテンシャル本命群 トラッキング（{asof}）", ""]
    if prev_path:
        lines.append(f"- 前回スナップショット: `{Path(prev_path).name}` との差分")
    else:
        lines.append("- **初回スナップショット（ベースライン確立）**。次回以降、DPS-TTMの増減で増配を検出する。")
    lines += ["", "| コード | 銘柄 | グループ | DPS-TTM | 前回比 | 配当性向 | 利回り | PBR |",
              "|---|---|---|---|---|---|---|---|"]
    inc = []
    for s in snap:
        pv = prev.get(s["code"])
        delta = ""
        if pv and to_f(pv.get("DPS_TTM")) is not None and s["DPS_TTM"] is not None:
            d = s["DPS_TTM"] - to_f(pv["DPS_TTM"])
            if d > 0.001:
                delta = f"**増配 +{round(d,2)}**"; inc.append(s["銘柄"])
            elif d < -0.001:
                delta = f"減配 {round(d,2)}"
            else:
                delta = "据置"
        lines.append(f"| {s['code']} | {s['銘柄']} | {s['グループ']} | {s['DPS_TTM']} | {delta} | "
                     f"{s['配当性向%']}% | {s['利回り%']}% | {s['PBR']} |")
    lines += ["", "## 増配トラッキングの見方",
              "- **DPS-TTM（直近12か月の1株配当）が前回比プラス＝増配を実行**。仮説（累進/DOE/中計）が実行に移ったサイン。",
              "- 据置・減配が続く場合は仮説の再検証（中計進捗・方針変更の有無）。",
              "- 大型（バンダイナムコ=DOE下限3.60%、住友不動産=累進12期）は減配しない方針＝下振れ限定を確認。"]
    if inc:
        lines += ["", f"### 今回増配を検出: {', '.join(inc)}"]
    lines += ["", "## 次回アクション（決算シーズンに手動確認）",
              "- 各社の期末/中間の配当予想修正・自己株買い決議（TDnet）",
              "- 中計進捗（最終年度目標に対する進捗率／上方・下方修正）＝鉄則",
              "- コナミ(9766)は数値中計非開示のため、実績増配の継続性を重点監視"]
    rep.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nスナップショット: {cur_path}")
    print(f"レポート: {rep}")
    if inc:
        print(f"増配検出: {', '.join(inc)}")


if __name__ == "__main__":
    main()
