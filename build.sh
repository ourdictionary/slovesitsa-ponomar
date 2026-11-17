#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Словесица (slovesitsa.org)
# Собрать все файлы заново из «Пономаря» и сверить с SHA256SUMS.
#   git clone --bare https://github.com/typiconman/Ponomar.git ../Ponomar.git
#   bash build.sh ../Ponomar.git
# Нужен Python 3.9 или новее, без сторонних пакетов.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: bash build.sh PATH/Ponomar.git" >&2; exit 2; }
G="$(cd "$1" && pwd)"
C=0af645f438856f45c22026912d2e4a9ce495e531
cd "$(dirname "$0")"
BOOKS=Psalm,Mt,Mk,Lk,Jn,Acts,Jas,I_Pet,II_Pet,I_Jn,II_Jn,III_Jn,Jude,Rom,I_Cor,II_Cor,Gal,Eph,Philip,Col,I_Thess,II_Thess,I_Tim,II_Tim,Tit,Philemon,Heb,Apoc

mkdir -p build
python3 -I ponomar_text.py build --git-dir "$G" --commit $C --out build --books $BOOKS \
  --corrections corrections/bible.tsv --zachalo corrections/zachalo.tsv > build/nt.log
{ printf 'book\tkind\tchapters\tverses\theadings\n'; grep -v -E '^(corrections|zachalo)	' build/nt.log; } > build/counts.tsv
grep -E '^(corrections|zachalo)	' build/nt.log
rm build/nt.log
(cd ot && python3 -I ot_build.py --git-dir "$G" --commit $C --out build > /dev/null)
python3 -I kathisma.py build --git-dir "$G" --commit $C --psalm-json build/elis/Psalm.json \
  --corrections corrections --out build/kathismas.json > /dev/null
(cd hours && python3 -I hours_build.py --git-dir "$G" --commit $C --out ../build/hours | grep -E '^правок')

if command -v sha256sum > /dev/null; then sha256sum -c --quiet SHA256SUMS; else shasum -a 256 -c --quiet SHA256SUMS; fi
echo "OK: все файлы совпали с SHA256SUMS"
