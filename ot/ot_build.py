#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Словесица (slovesitsa.org)
"""Ветхий Завет: сборка книг ВЗ тем же разборщиком, что Псалтирь и НЗ (../ponomar_text.py, не меняется).

Пять мест в исходнике Пономаря разборщик не знает; они переносятся до разбора, ничего не теряется:
- предисловие перед главой 1 (синодальный Сирах, Есфирь) -> надписание главы 1 одной строкой, как у славянского Сираха;
- молитва Манассии после 2 Пар 36 без номеров (синодальный) -> стих 36, 24, как у славянского;
- хвост после последнего стиха (синодальный Иов: добавление и примечания; славянское Бытие: «Конецъ книзѣ…»)
  -> поле "appendix" у последней главы: список строк того же вида, что стих, без номера.
Любая другая строка не по формату — отказ разборщика, как и раньше.

python3 -I ot_build.py --git-dir ПУТЬ/Ponomar.git --commit 0af645f4 --out build
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ponomar_text as pt  # noqa: E402

BOOKS = ("Gen Ex Lev Num Deut Josh Judg Ruth I_Kings II_Kings III_Kings IV_Kings I_Paral II_Paral I_Esdra Nehem "
         "Esther Job Prov Eccles Song Isa Jerem Lamen Ezek Dan Hos Joel Amos Obad Jona Mica Nahum Habak Zeph Hagg "
         "Zech Mal II_Esdra III_Esdra Tobit Judith Wisd Sirach Epistle Baruch I_Macc II_Macc III_Macc").split()
PATHS = {"elis": "Ponomar/languages/cu/bible/elis/%s.text", "synod": "Ponomar/languages/cu/ru/bible/synod/%s.text"}
CH = re.compile(r"#\d+")
VS = re.compile(r"(\d+)\|(.*)")


def _ok(line):
    s = line.strip()
    return not s or CH.fullmatch(s) or VS.fullmatch(s)


def prepare(text, kind, book):
    """Текст для разборщика + строки хвоста. Возвращает (text, appendix_lines, moved) — moved: что куда перенесено."""
    lines = text.lstrip("﻿").split("\n")
    first_ch = next(i for i, l in enumerate(lines) if CH.fullmatch(l.strip()))
    last_vs = max(i for i, l in enumerate(lines) if VS.fullmatch(l.strip()))
    head = [l.strip() for l in lines[:first_ch] if l.strip() and l.strip() != "=="]
    tail = [l.strip() for l in lines[last_vs + 1:] if l.strip() and l.strip() != "=="]
    if any(CH.fullmatch(l) for l in tail):
        sys.exit("%s %s: глава после хвоста" % (book, kind))
    body = lines[first_ch:last_vs + 1]
    moved = []
    appendix = []
    if head:
        joined = " ".join(VS.fullmatch(l).group(2).strip() if VS.fullmatch(l) else l for l in head)
        if kind != "synod":
            sys.exit("%s %s: предисловие у славянского текста — не предусмотрено" % (book, kind))
        body = [body[0], joined] + body[1:]
        moved.append("предисловие (%d строк) -> надписание главы 1" % len(head))
    if tail:
        if book == "II_Paral" and kind == "synod":
            last_n = int(VS.fullmatch(lines[last_vs].strip()).group(1))
            body.append("%d|%s" % (last_n + 1, " ".join(tail)))
            moved.append("молитва Манассии (%d строк) -> стих %d" % (len(tail), last_n + 1))
        else:
            appendix = tail
            moved.append("хвост (%d строк) -> appendix последней главы" % len(tail))
    return "\n".join(body) + "\n", appendix, moved


SPLIT = HERE / "split_ex36_39.tsv"


def apply_split(data):
    """Исх 36-39 (славянский): по одному «стиху» на главу -> куски по границам azbyka (таблица SPLIT, ни одного слова не теряется).
    Куску даются: n (уникален в главе), syn — синодальное место «глава:стих», print — печатный номер славянского (если есть)."""
    rows = [l.split("\t") for l in SPLIT.read_text(encoding="utf-8").split("\n")[1:] if l.strip()]
    by = {}
    for r in rows:
        by.setdefault(int(r[0]), []).append(r)
    made = 0
    for ch in data["chapters"]:
        if ch["n"] not in (36, 37, 38, 39):
            continue
        for v in ch["verses"][:-1]:  # у 36-й главы стихи 1-7 целые: только синодальное место
            v["syn"] = "%d:%d" % (ch["n"], v["n"])
        last = ch["verses"][-1]
        assert last.get("zachalo") == [] and not last["lead"], (ch["n"], "zachalo/lead")
        toks = last["tokens"]
        pieces = []
        for r in by[ch["n"]]:
            a, b = int(r[4]), int(r[5])
            t = [dict(x) for x in toks[a:b]]
            t[-1]["s"] = t[-1]["s"].rstrip(" ")
            piece = {"n": int(r[1]), "text": "".join(x["w"] + x["s"] for x in t), "lead": "", "tokens": t, "zachalo": [], "syn": r[2]}
            if r[3]:
                piece["print"] = r[3]
            pieces.append(piece)
        # проверка-отказ: ни слова не потеряно и не добавлено, порядок тот же, склейка кусков через пробел = прежний текст
        assert [x["w"] for p in pieces for x in p["tokens"]] == [x["w"] for x in toks], ch["n"]
        assert " ".join(p["text"] for p in pieces) == last["text"], ch["n"]
        assert len({p["n"] for p in pieces}) == len(pieces), ch["n"]
        ch["verses"][-1:] = pieces
        made += len(pieces)
    data["counts"]["verses"] = sum(len(c["verses"]) for c in data["chapters"])
    return made


def main(argv):
    o = dict(zip(argv[0::2], argv[1::2]))
    git_dir, commit, out = o["--git-dir"], o["--commit"], Path(o["--out"])
    sha = pt._git(git_dir, ["rev-parse", commit]).stdout.decode().split()[0]
    for book in BOOKS:
        for kind, tpl in PATHS.items():
            path = tpl % book
            r = pt._git(git_dir, ["show", "%s:%s" % (sha, path)])
            if r.returncode != 0:
                sys.exit("git show %s: %s" % (path, r.stderr.decode()))
            raw = r.stdout.decode("utf-8")
            text, appendix, moved = prepare(raw, kind, book)
            src = {"repo": "typiconman/Ponomar", "commit": sha, "path": path, "licence": "GPL-3.0-or-later"}
            data = pt.build_book(text, kind, book, src)
            if book == "Ex" and kind == "elis":
                moved.append("Исх 36-39 разделены по azbyka: %d кусков" % apply_split(data))
            if appendix:
                data["chapters"][-1]["appendix"] = [pt._parse_item(l, None, kind, 0) for l in appendix]
            if moved:
                data["moved"] = moved
            # Ни одна непустая строка исходника не потеряна: число строк до = после переноса.
            src_lines = sum(1 for l in raw.lstrip("﻿").split("\n") if l.strip() and l.strip() != "==")
            kept = sum(1 for l in text.split("\n") if l.strip()) + len(appendix)
            extra = sum(int(re.search(r"\((\d+) строк", m).group(1)) - 1 for m in moved if "-> надписание" in m or "-> стих" in m)
            if src_lines != kept + extra:
                sys.exit("%s %s: строк в исходнике %d, после переноса %d" % (book, kind, src_lines, kept + extra))
            (out / kind).mkdir(parents=True, exist_ok=True)
            (out / kind / ("%s.json" % book)).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            c = data["counts"]
            print("%s\t%s\t%d\t%d\t%d\t%s" % (book, kind, c["chapters"], c["verses"], c["headings"], "; ".join(moved)))


if __name__ == "__main__":
    main(sys.argv[1:])
