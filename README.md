# review-quatorperform — 四半期還元レビュー（専用最小リポジトリ）

LifePlanning / 投資戦略エージェントの「株主還元パイプライン」を、四半期ごとに
クラウドで自動実行するための最小リポジトリ。**個人の保有データ・秘密情報は含まない**
（銘柄コードのウォッチリストと採点ロジックのみ。データは実行時に yfinance から取得）。

## 構成
- `scripts/` — 採点パイプライン
  - `compute_shareholder_return_capacity.py` … 株主還元余地(最大30)
  - `detect_return_execution.py` … 還元実行(増配/自社株買い, 分割補正・累進性判定)
  - `rank_tierA_forward.py` … 前向きスコア(最大20)
  - `finalize_tierA_ranking.py` … 方針スコア(IR確認, 最大6)
  - `quarterly_review.py` … 上記を統合し四半期スナップショット＋前回差分＋要Web確認レポートを生成
- `data/watchlist.csv` — 追跡対象108社（コード・銘柄・業種）
- `reports/data/quarterly/` — 四半期スナップショット（差分の基準）
- `CLAUDE.md` — 分析の鉄則（特に「最も遠い業績予測=中計最終年度まで評価する」）

## 実行
```
pip install -r requirements.txt
python scripts/quarterly_review.py
```
`reports/YYYYMMDD_quarterly_review.md`（要Web確認リスト付き雛形）と
`reports/data/quarterly/snapshot_YYYYMMDD.csv` を生成する。

## 運用（四半期ルーチン）
決算シーズン連動（2/5/8/11月の15日）で自動実行し、要Web確認銘柄の最新決算・配当予想修正・
自己株進捗・中計最終年度目標をWeb確認してレポートに追記、社長へGmail通知する。
