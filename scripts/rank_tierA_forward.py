"""Tier A 銘柄を「増配持続性（前向き度）」で再ランク付けする。

背景:
  detect_return_execution.py(v2) の Tier A は「過去に実行した」実績ベース。
  買える銘柄を絞るため、「これから増配・還元を続ける蓋然性」を前向き指標で採点する。

前向き指標（データで全社に付与）:
  ① 予想増配率 : 会社予想の来期年間配当(yfinance dividendRate) ÷ 直近実績配当 − 1
                → 会社が"来期の増配を予想/計画"しているかの直接シグナル。
                  ※ yfinance の trailingAnnualDividendRate は時々壊れるため、
                     基準は自前の会計年度配当(直近)を使う。
  ② 累進年数   : 連続非減配年数。長いほど方針としての継続力が強く、継続を予測。
  ③ 増益率     : earningsGrowth。増配の原資となる利益の方向。
  ④ 配当性向余地: 低いほど増配余地（ただし極端な低さは還元姿勢弱として抑制）。

増配持続性スコア(最大20) = 予想増配(8) + 累進年数(6) + 増益(3) + 配当性向余地(3)

入力: data/screening/還元実行_v2_20260702.csv （自動Tier が A / A- の銘柄）
出力: data/screening/TierA_前向きランク_20260703.csv

実行: python scripts/rank_tierA_forward.py
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SCREEN_DIR = ROOT / "data" / "screening"
IN_CSV = SCREEN_DIR / "還元実行_v2_20260702.csv"
OUT_CSV = SCREEN_DIR / "TierA_前向きランク_20260703.csv"
NOW = pd.Timestamp("2026-07-03").normalize()


def _tznaive(idx):
    return idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx


def fy_dividends(t):
    try:
        d = t.dividends
    except Exception:
        return pd.Series(dtype=float)
    if d is None or len(d) == 0:
        return pd.Series(dtype=float)
    d = d.copy()
    d.index = _tznaive(d.index)
    return d.groupby(d.index.to_series().apply(
        lambda x: x.year - 1 if x.month <= 3 else x.year)).sum()


def progressive_years(fy):
    vals = [float(v) for v in fy.values]
    prog = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] >= vals[i - 1] * 0.999:
            prog += 1
        else:
            break
    return prog


def s_forecast(rate):
    if rate is None:
        return 3, "予想不明"          # 中立
    if rate >= 8:
        return 8, f"来期増配予想 +{rate:.0f}%"
    if rate >= 4:
        return 6, f"来期増配予想 +{rate:.0f}%"
    if rate >= 1:
        return 4, f"来期小幅増配 +{rate:.0f}%"
    if rate > -1:
        return 2, "来期据置予想"
    return 0, f"来期減配予想 {rate:.0f}%"


def s_prog(y):
    return 6 if y >= 5 else 5 if y == 4 else 4 if y == 3 else 2 if y == 2 else 0


def s_growth(g):
    if g is None:
        return 1, "増益率不明"
    if g >= 0.10:
        return 3, f"増益 +{g*100:.0f}%"
    if g >= 0:
        return 2, f"小幅増益 +{g*100:.0f}%"
    return 0, f"減益 {g*100:.0f}%"


def s_payout(p):
    if p is None:
        return 1
    if 0.15 <= p <= 0.45:
        return 3
    if p < 0.15 or 0.45 < p <= 0.60:
        return 2
    return 0


def main():
    rows = list(csv.DictReader(IN_CSV.open(encoding="utf-8-sig")))
    tierA = [r for r in rows if r["自動Tier"] in ("A", "A-")]
    print(f"Tier A/A- {len(tierA)}社を前向き度で再ランク")

    out = []
    for i, r in enumerate(tierA):
        code = r["コード"]
        t = yf.Ticker(code + ".T")
        info = {}
        try:
            info = t.info
        except Exception:
            pass
        fy = fy_dividends(t)
        latest_fy = float(fy.iloc[-1]) if len(fy) else None
        fwd = info.get("dividendRate")
        # 予想増配率は自前FY実績を基準に(yfinance trailingは不安定)
        fcast = ((fwd / latest_fy - 1) * 100) if (fwd and latest_fy) else None
        prog = progressive_years(fy) if len(fy) else 0
        growth = info.get("earningsGrowth")
        payout = info.get("payoutRatio")

        p_f, note_f = s_forecast(fcast)
        p_p = s_prog(prog)
        p_g, note_g = s_growth(growth)
        p_pay = s_payout(payout)
        fwd_score = p_f + p_p + p_g + p_pay

        out.append({
            "コード": code, "銘柄": r["銘柄"], "業種": r["業種"], "PBR": r["PBR"],
            "旧Tier": r["自動Tier"], "総合(余地+実行)": r["総合(余地+実行)"],
            "配当推移": r["配当推移"], "直近配当": latest_fy, "会社予想配当": fwd,
            "予想増配率%": (round(fcast, 1) if fcast is not None else ""),
            "累進年数": prog, "増益率%": (round(growth * 100, 0) if growth is not None else ""),
            "配当性向": (round(payout, 3) if payout is not None else ""),
            "予想点": p_f, "累進点": p_p, "増益点": p_g, "性向点": p_pay,
            "増配持続性スコア": fwd_score,
            "前向き根拠": f"{note_f}／累進{prog}年／{note_g}",
        })
        time.sleep(0.2)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(tierA)}")

    out.sort(key=lambda x: (-x["増配持続性スコア"], -int(x["総合(余地+実行)"])))
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"出力: {OUT_CSV}\n")

    print("=== Tier A 前向きランキング（増配持続性スコア降順）===")
    for rank, r in enumerate(out, 1):
        print(f"{rank:2d}. {r['増配持続性スコア']:2d}点 {r['銘柄'][:15]:15s} PBR{r['PBR']} "
              f"[{r['前向き根拠']}] 性向{r['配当性向']}")


if __name__ == "__main__":
    main()
