"""スクリーニング銘柄に「株主還元余地スコア」を付与する。

目的:
  直近5年でTOPIXを超えた大和住銀DC国内株式ファンドの超過リターン源泉
  （= 大型バリュー + 株主還元余地 = 東証改革/PBR是正テーマ）のうち、
  社長の既存スクリーニング(PBR/ROA/エントリー/規模)に欠けていた
  「還元する体力（ネットキャッシュ・配当性向の余地・財務健全性）」を
  定量化し、低PBR銘柄の中から自社株買い・増配で再評価されやすい銘柄を
  浮かび上がらせる。

入力:
  data/screening/スクリーニング2_20260624.csv  (478行, 列: 銘柄/株価/前年比騰落率/時価総額/PBR/ROA/業種)

出力:
  reports/data/yf_fundamentals_cache.csv          … yfinance生データのキャッシュ(再実行高速化)
  data/screening/株主還元余地_20260630.csv          … 元データ + 財務指標 + 株主還元余地スコア

株主還元余地スコア(最大30点) = ネットキャッシュ比率(15) + 配当性向余地(8) + 財務健全性D/E(7)
  - ネットキャッシュ比率 = (現預金等 - 有利子負債) / 時価総額。高いほど自社株買い余地大。
  - 配当性向余地: 20〜40%が最高(健全かつ増配余地)。低すぎ=還元姿勢弱、高すぎ=余地小。
  - 財務健全性: D/E(負債資本倍率)が低いほど高得点。

実行:
  python scripts/compute_shareholder_return_capacity.py
  (478銘柄を yfinance から取得。キャッシュがあれば再取得しない)
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SCREEN_DIR = ROOT / "data" / "screening"
IN_CSV = SCREEN_DIR / "スクリーニング2_20260624.csv"
OUT_CSV = SCREEN_DIR / "株主還元余地_20260630.csv"
CACHE = ROOT / "reports" / "data" / "yf_fundamentals_cache.csv"

YF_FIELDS = ["totalCash", "totalDebt", "marketCap", "payoutRatio",
             "dividendYield", "returnOnEquity", "debtToEquity"]


def code_of(meigara: str) -> str:
    return meigara.split()[0]


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    out = {}
    for r in csv.DictReader(CACHE.open(encoding="utf-8")):
        out[r["code"]] = r
    return out


def save_cache(cache: dict):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cols = ["code"] + YF_FIELDS
    with CACHE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for code, rec in cache.items():
            w.writerow({"code": code, **{k: rec.get(k, "") for k in YF_FIELDS}})


def fetch(code: str) -> dict:
    for _ in range(3):
        try:
            info = yf.Ticker(code + ".T").info
            return {k: info.get(k) for k in YF_FIELDS}
        except Exception:
            time.sleep(1.0)
    return {k: None for k in YF_FIELDS}


def to_float(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- スコアリング ----
def score_netcash_ratio(nc_ratio):
    if nc_ratio is None:
        return 0
    for thr, pt in [(0.50, 15), (0.35, 13), (0.20, 11), (0.10, 9), (0.00, 6),
                    (-0.25, 3)]:
        if nc_ratio >= thr:
            return pt
    return 0


def score_payout(payout):
    if payout is None or payout <= 0 or payout > 1.5:
        return 0
    if 0.20 <= payout <= 0.40:
        return 8
    if 0.10 <= payout < 0.20:
        return 7
    if 0.40 < payout <= 0.55:
        return 6
    if 0 < payout < 0.10:
        return 5
    if 0.55 < payout <= 0.75:
        return 4
    if 0.75 < payout <= 1.0:
        return 2
    return 0  # >100% (赤字補填的)


def score_de(de):
    if de is None:
        return 0
    for thr, pt in [(25, 7), (50, 6), (75, 4), (125, 2), (200, 1)]:
        if de <= thr:
            return pt
    return 0


def main():
    rows = list(csv.DictReader(IN_CSV.open(encoding="utf-8")))
    cache = load_cache()
    print(f"母集団 {len(rows)}銘柄 / キャッシュ済 {len(cache)}件")

    fetched = 0
    for i, r in enumerate(rows):
        code = code_of(r["銘柄"])
        if code not in cache:
            cache[code] = fetch(code)
            fetched += 1
            time.sleep(0.3)
            if fetched % 25 == 0:
                save_cache(cache)
                print(f"  取得 {fetched}件 (進捗 {i+1}/{len(rows)})")
    save_cache(cache)
    print(f"取得完了 (新規 {fetched}件)")

    out_cols = list(rows[0].keys()) + [
        "ネットキャッシュ比率", "配当性向", "配当利回り%", "D/E%", "ROE%",
        "NC余地点", "配当余地点", "健全性点", "株主還元余地スコア", "データ欠損"]
    enriched = []
    for r in rows:
        code = code_of(r["銘柄"])
        rec = cache.get(code, {})
        cash = to_float(rec.get("totalCash"))
        debt = to_float(rec.get("totalDebt"))
        mcap = to_float(rec.get("marketCap"))
        payout = to_float(rec.get("payoutRatio"))
        dy = to_float(rec.get("dividendYield"))
        roe = to_float(rec.get("returnOnEquity"))
        de = to_float(rec.get("debtToEquity"))

        nc_ratio = ((cash - debt) / mcap) if (cash is not None and debt is not None
                                              and mcap) else None
        yld = (dy * 100 if dy is not None and dy < 1 else dy)  # 旧形式(小数)を%へ

        s_nc = score_netcash_ratio(nc_ratio)
        s_po = score_payout(payout)
        s_de = score_de(de)
        total = s_nc + s_po + s_de
        missing = [f for f, v in [("NC", nc_ratio), ("payout", payout), ("D/E", de)]
                   if v is None]

        row = dict(r)
        row.update({
            "ネットキャッシュ比率": f"{nc_ratio:.3f}" if nc_ratio is not None else "",
            "配当性向": f"{payout:.3f}" if payout is not None else "",
            "配当利回り%": f"{yld:.2f}" if yld is not None else "",
            "D/E%": f"{de:.1f}" if de is not None else "",
            "ROE%": f"{roe*100:.1f}" if roe is not None else "",
            "NC余地点": s_nc, "配当余地点": s_po, "健全性点": s_de,
            "株主還元余地スコア": total,
            "データ欠損": "/".join(missing),
        })
        enriched.append(row)

    enriched.sort(key=lambda x: -x["株主還元余地スコア"])
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        w.writerows(enriched)
    print(f"出力: {OUT_CSV}")

    # 上位20と、低PBR(≤0.8)×高還元余地(≥20)の「PBR是正候補」を表示
    print("\n=== 株主還元余地スコア 上位15 ===")
    for r in enriched[:15]:
        print(f"  {r['株主還元余地スコア']:2d}点 {r['銘柄'][:18]:18s} "
              f"PBR{r['PBR']} NC比{r['ネットキャッシュ比率']} "
              f"配当性向{r['配当性向']} D/E{r['D/E%']}")

    def pbr_val(s):
        return to_float(s.replace("倍", "")) if s else None
    cands = [r for r in enriched
             if (pbr_val(r["PBR"]) is not None and pbr_val(r["PBR"]) <= 0.8
                 and r["株主還元余地スコア"] >= 20)]
    print(f"\n=== PBR≤0.8 かつ 還元余地≥20点 の「PBR是正候補」: {len(cands)}社 ===")
    for r in cands[:20]:
        print(f"  {r['株主還元余地スコア']:2d}点 {r['銘柄'][:18]:18s} "
              f"PBR{r['PBR']} ROA{r['ROA']} NC比{r['ネットキャッシュ比率']}")


if __name__ == "__main__":
    main()
