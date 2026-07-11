"""「PBR是正候補」の中から、実際に株主還元を"実行"した銘柄を検出する。（v2: 分割補正・累進性判定）

位置づけ:
  compute_shareholder_return_capacity.py が出す「株主還元"余地"」は
  バランスシート上の体力に過ぎない。本スクリプトは市場データから
  "実行済み"の還元を捕捉し、余地をシグナルに変える。

v2での改善（2026-07-02 の一次資料確認で判明した弱点の恒久対策）:
  ① 分割検知: 発行済株数(get_shares_full)は分割で不連続になり自社株買いを誤検出する。
     .splits から測定窓内の分割倍率を求めて株数変化を補正し、過去2年に分割が
     あった銘柄は「分割あり(要確認)」フラグを立てて自社株買い判定の確信度を下げる。
  ② 累進性チェック: 単年TTM比較は記念/特別配・減配からの反発で膨らむ。
     会計年度ベースの配当全履歴から「累進増配/回復増配/急増(特別疑い)/
     ピーク割れ/減配」を分類し、質を区別する。
     （注: yfinanceの .dividends は分割調整済みのため配当側は分割補正不要）

入力:  data/screening/株主還元余地_20260630.csv （PBR≤0.8 & 余地≥20 の108社）
出力:  data/screening/還元実行_v2_20260702.csv

実行:  python scripts/detect_return_execution.py
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SCREEN_DIR = ROOT / "data" / "screening"
IN_CSV = SCREEN_DIR / "株主還元余地_20260630.csv"
OUT_CSV = SCREEN_DIR / "還元実行_v2_20260702.csv"

PBR_MAX = 0.8
CAPACITY_MIN = 20
NOW = pd.Timestamp("2026-07-02").normalize()


def code_of(m):
    return m.split()[0]


def pbr_val(s):
    try:
        return float(s.replace("倍", ""))
    except (AttributeError, ValueError):
        return None


def _tznaive(idx):
    return idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx


# ---- ① 配当: 会計年度化 + 累進性分類（.dividends は分割調整済み） ----
def fiscal_year_dividends(t):
    """支払月が1-3月なら前年度に寄せて会計年度別の年間配当を返す。"""
    try:
        d = t.dividends
    except Exception:
        return None
    if d is None or len(d) == 0:
        return pd.Series(dtype=float)
    d = d.copy()
    d.index = _tznaive(d.index)
    fy = d.groupby(d.index.to_series().apply(
        lambda x: x.year - 1 if x.month <= 3 else x.year)).sum()
    return fy


def dividend_quality(fy: pd.Series):
    """(TTM増配率相当のYoY, 分類ラベル, 増配フラグ, 配当スコア0-3)"""
    if fy is None or len(fy) < 2:
        return None, "配当データ不足", False, 0
    vals = [float(v) for v in fy.tail(6).values]
    latest, prev = vals[-1], vals[-2]
    yoy = (latest / prev - 1) * 100 if prev > 0 else None
    peak_prior = max(vals[:-1]) if len(vals) > 1 else latest

    # 過去の正のYoY増分の中央値（急増判定の基準）
    pos_steps = []
    for i in range(1, len(vals)):
        if vals[i - 1] > 0 and vals[i] > vals[i - 1]:
            pos_steps.append(vals[i] / vals[i - 1] - 1)
    med_step = sorted(pos_steps)[len(pos_steps) // 2] if pos_steps else None

    # 連続非減配年数（累進性）
    prog = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] >= vals[i - 1] * 0.999:
            prog += 1
        else:
            break

    if latest < prev * 0.98:
        return yoy, "減配", False, 0
    if latest < peak_prior * 0.9:
        # 増えてはいるが過去ピークに未達＝下落基調内の反発（質低）
        return yoy, "ピーク割れ(質注意)", True, 1
    if yoy is not None and yoy >= 40 and med_step is not None and (yoy / 100) >= 2.5 * med_step:
        return yoy, "急増(特別/記念の内訳要確認)", True, 2
    if prog >= 3:
        return yoy, "累進増配", True, 3
    if yoy is not None and yoy >= 2:
        return yoy, "増配", True, 2
    return yoy, "横ばい", False, 0


# ---- ② 自社株買い: 分割補正した株数変化 + 分割フラグ ----
def share_change_adjusted(t):
    """(分割補正後の12か月株数変化%, 直近2年に分割あり, 分割メモ)"""
    try:
        s = t.get_shares_full(start=(NOW - pd.DateOffset(years=2)).date().isoformat())
    except Exception:
        return None, False, ""
    if s is None or len(s) < 2:
        return None, False, ""
    s = s.copy()
    s.index = _tznaive(s.index)
    s = s.dropna()

    try:
        sp = t.splits
        sp.index = _tznaive(sp.index)
    except Exception:
        sp = pd.Series(dtype=float)
    sp2y = sp[sp.index > NOW - pd.DateOffset(years=2)]
    split_flag = len(sp2y) > 0
    split_note = "/".join(f"{d.date()}:{r:g}" for d, r in sp2y.items()) if split_flag else ""

    latest = float(s.iloc[-1])
    base_ser = s[s.index <= NOW - pd.DateOffset(years=1)]
    if len(base_ser):
        base, base_date = float(base_ser.iloc[-1]), base_ser.index[-1]
    else:
        base, base_date = float(s.iloc[0]), s.index[0]
    # 測定窓(base_date, NOW]内の分割倍率で base を今日基準へ換算
    win = sp[(sp.index > base_date) & (sp.index <= NOW)]
    factor = float(win.prod()) if len(win) else 1.0
    base_adj = base * factor
    if base_adj == 0:
        return None, split_flag, split_note
    return round((latest / base_adj - 1) * 100, 2), split_flag, split_note


def buyback_class(adj_chg, split_flag):
    """(自社株買いラベル, スコア0-3)。分割ありは確信度を下げる。"""
    if adj_chg is None:
        return "不明", 0
    if split_flag:
        # 分割補正はしたが、買いのタイミングと分割の重なりで不確実 → 要確認扱い
        if adj_chg <= -1.0:
            return "自社株買い?(分割あり要確認)", 1
        return "なし(分割あり)", 0
    if adj_chg <= -3.0:
        return "自社株買い(大)", 3
    if adj_chg <= -1.0:
        return "自社株買い", 2
    return "なし", 0


def auto_tier(div_score, div_label, bb_label, bb_score):
    zoshi = div_score >= 2
    buyback = bb_score >= 2
    if not (zoshi and (bb_score >= 1)):
        return "-"  # 両輪でない
    if div_label == "累進増配" and buyback:
        return "A"      # 累進増配 × 明確な買い
    if "分割あり" in bb_label or div_label in ("ピーク割れ(質注意)", "急増(特別/記念の内訳要確認)"):
        return "B"      # 質に注意
    return "A-"


def main():
    rows = list(csv.DictReader(IN_CSV.open(encoding="utf-8-sig")))
    cands = [r for r in rows
             if pbr_val(r["PBR"]) is not None and pbr_val(r["PBR"]) <= PBR_MAX
             and int(r["株主還元余地スコア"]) >= CAPACITY_MIN]
    print(f"PBR是正候補 {len(cands)}社を再判定（v2: 分割補正・累進性）")

    out = []
    for i, r in enumerate(cands):
        code = code_of(r["銘柄"])
        t = yf.Ticker(code + ".T")
        fy = fiscal_year_dividends(t)
        yoy, dlabel, zoshi, dscore = dividend_quality(fy)
        adj, split_flag, split_note = share_change_adjusted(t)
        blabel, bscore = buyback_class(adj, split_flag)
        # 両輪判定は「配当点≥2(横ばい/ピーク割れ/減配を除く実質増配)」×「買い点≥1」で統一
        both = dscore >= 2 and bscore >= 1
        tier = auto_tier(dscore, dlabel, blabel, bscore)
        hist = "→".join(f"{v:g}" for v in (fy.tail(5).values if fy is not None and len(fy) else []))
        out.append({
            "コード": code, "銘柄": r["銘柄"], "業種": r["業種"], "PBR": r["PBR"], "ROA": r["ROA"],
            "株主還元余地": int(r["株主還元余地スコア"]),
            "配当推移": hist, "増配率%": yoy, "配当質": dlabel, "配当点": dscore,
            "株数変化%(分割補正)": adj, "分割": split_note, "自社株買い判定": blabel, "買い点": bscore,
            "両輪": "○" if both else "", "実行スコア": dscore + bscore,
            "総合(余地+実行)": int(r["株主還元余地スコア"]) + dscore + bscore,
            "自動Tier": tier,
        })
        time.sleep(0.2)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(cands)}")

    out.sort(key=lambda x: (-int(x["両輪"] == "○"), -x["総合(余地+実行)"]))
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"出力: {OUT_CSV}\n")

    both = [r for r in out if r["両輪"] == "○"]
    from collections import Counter
    print(f"=== 両輪(増配×自社株買い): {len(both)}社  Tier内訳: {dict(Counter(r['自動Tier'] for r in both))} ===")
    for r in both:
        print(f"  総合{r['総合(余地+実行)']:2d} [{r['自動Tier']:2s}] {r['銘柄'][:16]:16s} PBR{r['PBR']} "
              f"配当[{r['配当質']}] {r['配当推移']} | 株数{r['株数変化%(分割補正)']}%"
              f"{' 分割:'+r['分割'] if r['分割'] else ''}")

    # v1で両輪だったが分割補正で買いが消えた銘柄(=訂正)
    print("\n=== 参考: 分割フラグ付き（自社株買い判定に注意）===")
    for r in out:
        if r["分割"]:
            print(f"  {r['銘柄'][:16]:16s} 分割[{r['分割']}] → 買い判定[{r['自社株買い判定']}] 株数{r['株数変化%(分割補正)']}%")


if __name__ == "__main__":
    main()
