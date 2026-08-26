#!/bin/bash
# ============================================================
#  ボンディング回線スループット計測 - かんたん起動 (Mac)
#  このファイルをダブルクリックすると、日本語の対話メニューが
#  立ち上がります。番号を選んでEnterを押すだけで計測できます。
#  (初回は「開発元を確認できない」と出た場合、右クリック→開く)
# ============================================================
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python3 が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
    read -r -p "Enterで閉じます"
    exit 1
fi

# 初回のみ依存パッケージ(PyYAML)を入れる
if ! python3 -c "import yaml" >/dev/null 2>&1; then
    echo "初回準備中です。しばらくお待ちください..."
    python3 -m pip install --quiet -r requirements.txt
fi

python3 run.py --wizard
read -r -p "Enterで閉じます"
