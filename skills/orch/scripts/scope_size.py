#!/usr/bin/env python3
"""対象スコープの規模実測ツール(orch型 v0.4)。

読み込み分担のROI分岐(model-routing.md)に使う「既存資産ボリューム」を
1コマンドで実測する。WP切り出し・見積りの前に対象ディレクトリ/ファイルに対して実行し、
推定トークンから直読/ハイブリッド/蒸留必須の帯を判定する。

使い方:
  python3 scope_size.py <path> [<path>...]
  (ディレクトリはコード系拡張子を再帰集計。.git や node_modules 等は除外)

トークン推定: コード≈bytes/4、日本語文書≈bytes/3 の粗い近似(判断には帯が分かれば十分)。
帯: ~100k tok=直読圏 / 100k〜500k=ハイブリッド / 500k超=蒸留必須(直読はピンポイントのみ)
"""
import os
import sys

CODE_EXT = {".py", ".sh", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".rs", ".java",
            ".c", ".h", ".cpp", ".sql", ".yaml", ".yml", ".json", ".toml", ".css", ".html", ".astro"}
DOC_EXT = {".md", ".txt", ".rst"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".next", ".astro", "coverage", ".openclaw"}
SKIP_FILES_OVER = 2_000_000  # 2MB超はモデル等のバイナリとみなし除外


def walk(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        else:
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    yield os.path.join(root, f)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    files = code_lines = code_bytes = doc_bytes = 0
    for path in walk(sys.argv[1:]):
        ext = os.path.splitext(path)[1].lower()
        if ext not in CODE_EXT and ext not in DOC_EXT:
            continue
        try:
            size = os.path.getsize(path)
            if size > SKIP_FILES_OVER:
                continue
            with open(path, "rb") as f:
                lines = f.read().count(b"\n")
        except OSError:
            continue
        files += 1
        if ext in CODE_EXT:
            code_lines += lines
            code_bytes += size
        else:
            doc_bytes += size
    est_tokens = code_bytes // 4 + doc_bytes // 3
    if est_tokens <= 100_000:
        band = "直読圏(~100k tok): 指令塔が全読してよい。委譲は固定費負け"
    elif est_tokens <= 500_000:
        band = "ハイブリッド(100k〜500k tok): 広さ=Sonnet圧縮+コアを指令塔が直読"
    else:
        band = "蒸留必須(500k tok超): Sonnet並列で圧縮。直読は判断点のピンポイントのみ"
    print(f"対象: {' '.join(sys.argv[1:])}")
    print(f"ファイル数: {files} / コード行数: {code_lines:,} / 推定トークン: {est_tokens/1000:.0f}k")
    print(f"帯: {band}")


if __name__ == "__main__":
    main()
