#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Словесица (slovesitsa.org)
"""Разбор файлов Библии «Пономаря» (Словесица, 11.11.2026)."""
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

CHAPTER_RE = re.compile(r"#(\d+)")
VERSE_RE = re.compile(r"(\d+)\|(.*)")
ZACHALO_RE = re.compile(r"\*\*\(([^)]*)\)\*\*\s*")
VARIANT_RE = re.compile(r"\s*\{([^}]*)\}")
SEP_CHARS = set(",.;:!?·«»()[]\"„“”—–-")


def _fail(lineno, msg):
    raise ValueError("line %d: %s" % (lineno, msg))


def _is_sep(ch):
    return ch.isspace() or ch in SEP_CHARS


def _split_text(text):
    n = len(text)
    i = 0
    while i < n and _is_sep(text[i]):
        i += 1
    lead = text[:i]
    tokens = []
    spans = []
    while i < n:
        start = i
        while i < n and not _is_sep(text[i]):
            i += 1
        word = text[start:i]
        sep_start = i
        while i < n and _is_sep(text[i]):
            i += 1
        tokens.append({"w": word, "s": text[sep_start:i]})
        spans.append((start, sep_start))
    return lead, tokens, spans


def _parse_item(raw, n, kind, lineno):
    text = unicodedata.normalize("NFC", raw)

    zachalo = []
    while True:
        m = ZACHALO_RE.search(text)
        if not m:
            break
        label = m.group(1)
        dm = re.search(r"\d+", label)
        pos_text = VARIANT_RE.sub("", text[:m.start()])
        pos = len(_split_text(pos_text)[1])
        zachalo.append({
            "n": int(dm.group()) if dm else None,
            "label": label,
            "pos": pos,
        })
        text = text[:m.start()] + text[m.end():]

    if "**" in text:
        _fail(lineno, "unparsed zachalo marker")

    variants = {}
    while True:
        m = VARIANT_RE.search(text)
        if not m:
            break
        prefix_tokens = _split_text(text[:m.start()])[1]
        if not prefix_tokens:
            _fail(lineno, "variant without preceding word")
        idx = len(prefix_tokens) - 1
        variants.setdefault(idx, []).append(m.group(1).strip())
        text = text[:m.start()] + text[m.end():]

    italic = None
    if kind == "synod":
        chars = []
        flags = []
        depth = 0
        i = 0
        while i < len(text):
            if text.startswith("<i>", i):
                depth += 1
                i += 3
            elif text.startswith("</i>", i):
                if depth == 0:
                    _fail(lineno, "unbalanced </i>")
                depth -= 1
                i += 4
            elif text[i] == "<":
                _fail(lineno, "unknown tag")
            else:
                chars.append(text[i])
                flags.append(depth > 0)
                i += 1
        if depth:
            _fail(lineno, "unbalanced <i>")
        clean = "".join(chars)
        left = len(clean) - len(clean.lstrip())
        text = clean.strip()
        italic = flags[left:left + len(text)]
    else:
        text = text.strip()

    lead, tokens, spans = _split_text(text)
    for idx, values in variants.items():
        if idx >= len(tokens):
            _fail(lineno, "variant without preceding word")
        tokens[idx]["var"] = values

    if italic is not None:
        for token, (start, end) in zip(tokens, spans):
            if any(italic[start:end]):
                token["it"] = True

    return {
        "n": n,
        "text": text,
        "lead": lead,
        "tokens": tokens,
        "zachalo": zachalo,
    }


def parse_file(text, kind):
    if kind not in ("elis", "synod"):
        raise ValueError("unknown kind: %s" % kind)

    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    chapters = []
    current = None
    prev_chapter = None
    prev_verse = None

    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line:
            continue

        m = CHAPTER_RE.fullmatch(line)
        if m:
            n = int(m.group(1))
            if prev_chapter is not None and n <= prev_chapter:
                _fail(lineno, "chapter number not increasing")
            current = {"n": n, "heading": None, "verses": []}
            chapters.append(current)
            prev_chapter = n
            prev_verse = None
            continue

        m = VERSE_RE.fullmatch(line)
        if m:
            if current is None:
                _fail(lineno, "verse before chapter")
            n = int(m.group(1))
            if prev_verse is not None and n <= prev_verse:
                _fail(lineno, "verse number not increasing")
            if n == 0 and (current["heading"] is not None or current["verses"]):
                _fail(lineno, "verse 0 is not first in chapter")

            item = _parse_item(m.group(2).strip(), n, kind, lineno)
            if kind == "elis" and n == 0:
                current["heading"] = item
            else:
                current["verses"].append(item)
            prev_verse = n
            continue

        if (kind == "synod" and current is not None
                and not current["verses"] and current["heading"] is None):
            current["heading"] = _parse_item(line, None, kind, lineno)
            continue

        _fail(lineno, "unexpected line")

    return {"chapters": chapters}


def _first_word(item):
    if not item or not item.get("tokens"):
        return None
    return item["tokens"][0]["w"]


def _chapter_first_word(chapter):
    heading = chapter.get("heading")
    if heading is not None:
        return _first_word(heading)
    verses = chapter.get("verses") or []
    if verses:
        return _first_word(verses[0])
    return None


def _numbered_count(chapters):
    total = 0
    for chapter in chapters:
        heading = chapter.get("heading")
        if heading is not None and heading.get("n") == 0:
            total += 1
        total += len(chapter.get("verses") or [])
    return total


def _unnumbered_heading_count(chapters):
    return sum(1 for chapter in chapters
               if chapter.get("heading") is not None
               and chapter["heading"].get("n") is None)


def verify(text, parsed, kind):
    fresh = parse_file(text, kind)
    parsed_chapters = parsed.get("chapters") or []
    fresh_chapters = fresh["chapters"]

    if len(parsed_chapters) != len(fresh_chapters):
        raise ValueError("chapter count mismatch")
    for got, expected in zip(parsed_chapters, fresh_chapters):
        if got.get("n") != expected["n"]:
            raise ValueError("chapter number mismatch")

    if _numbered_count(parsed_chapters) != _numbered_count(fresh_chapters):
        raise ValueError("verse count mismatch")
    if (_unnumbered_heading_count(parsed_chapters)
            != _unnumbered_heading_count(fresh_chapters)):
        raise ValueError("heading count mismatch")

    for got, expected in zip(parsed_chapters, fresh_chapters):
        if _chapter_first_word(got) != _chapter_first_word(expected):
            raise ValueError("chapter first word mismatch")


def build_book(text, kind, book, source):
    parsed = parse_file(text, kind)
    verify(text, parsed, kind)
    chapters = parsed["chapters"]
    verses = sum(len(chapter["verses"]) for chapter in chapters)
    headings = sum(1 for chapter in chapters
                   if chapter["heading"] is not None)
    return {
        "book": book,
        "kind": kind,
        "source": source,
        "counts": {
            "chapters": len(chapters),
            "verses": verses,
            "headings": headings,
        },
        "chapters": chapters,
    }


CORRECTION_FIELDS = ("book", "verse", "was", "became", "basis", "sheet", "who")
VERSE_REF_RE = re.compile(r"(\d+), (\d+)")


def _refuse(lineno, msg):
    sys.exit("refused line %d: %s" % (lineno, msg))


def _refuse_correction(row, msg):
    sys.exit("refused %s %s (%s): %s"
             % (row["book"], row["verse"], row["was"], msg))


def load_bible_corrections(path):
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "\t".join(CORRECTION_FIELDS):
        _refuse(1, "bad header")
    rows = []
    for lineno, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(CORRECTION_FIELDS):
            _refuse(lineno, "expected %d fields, got %d"
                    % (len(CORRECTION_FIELDS), len(fields)))
        row = dict(zip(CORRECTION_FIELDS, fields))
        if not VERSE_REF_RE.fullmatch(row["verse"]):
            _refuse(lineno, "bad verse %r" % row["verse"])
        row["was"] = unicodedata.normalize("NFC", row["was"])
        row["became"] = unicodedata.normalize("NFC", row["became"])
        rows.append(row)
    return rows


def _find_runs(tokens, needle):
    runs = []
    n = len(tokens)
    for i in range(n):
        acc = ""
        for j in range(i, n):
            if j > i:
                acc += tokens[j - 1]["s"]
            acc += tokens[j]["w"]
            if acc == needle:
                runs.append((i, j))
    return runs


def apply_bible_corrections(data, rows):
    if data.get("kind") != "elis":
        return []

    applied = []
    for row in rows:
        if row["book"] != data.get("book"):
            continue

        m = VERSE_REF_RE.fullmatch(row["verse"])
        if not m:
            _refuse_correction(row, "bad verse reference")
        chapter_no = int(m.group(1))
        verse_no = int(m.group(2))

        chapter = None
        for ch in data.get("chapters") or []:
            if ch.get("n") == chapter_no:
                chapter = ch
                break
        if chapter is None:
            _refuse_correction(row, "chapter not found")

        if verse_no == 0:
            verse = chapter.get("heading")
        else:
            verse = None
            for v in chapter.get("verses") or []:
                if v.get("n") == verse_no:
                    verse = v
                    break
        if verse is None:
            _refuse_correction(row, "verse not found")

        tokens = verse.get("tokens") or []
        was = unicodedata.normalize("NFC", row["was"])
        became = unicodedata.normalize("NFC", row["became"])

        runs = _find_runs(tokens, was)
        if len(runs) != 1:
            _refuse_correction(row, "found %d places" % len(runs))
        i, j = runs[0]
        k = j - i + 1

        new_words = became.split()
        m_count = len(new_words)

        if m_count == k:
            for off in range(k):
                tokens[i + off]["w"] = new_words[off]
        else:
            for off in range(k):
                if set(tokens[i + off]) - {"w", "s"}:
                    _refuse_correction(
                        row, "cannot merge word with extra fields")
            last_sep = tokens[j]["s"]
            replacement = []
            for idx, word in enumerate(new_words):
                sep = " " if idx < m_count - 1 else last_sep
                replacement.append({"w": word, "s": sep})
            tokens[i:j + 1] = replacement

        for z in verse.get("zachalo") or []:
            pos = z.get("pos")
            if pos is None:
                continue
            if i < pos <= j:
                _refuse_correction(row, "zachalo inside replaced run")
            if pos > j:
                z["pos"] = pos - (k - m_count)

        verse["text"] = (verse.get("lead") or "") + "".join(
            t["w"] + t["s"] for t in tokens)

        applied.append({field: row[field] for field in CORRECTION_FIELDS})

    data["corrections"] = applied
    return applied


ZACHALO_FIELDS = ("book", "verse_now", "n_now", "pos_now", "verse_new", "n_new", "pos_new", "basis")


def load_zachalo_fixes(path):
    """corrections/zachalo.tsv (16.11): пометка зачала, сверенная с печатью, — откуда (стих, номер, слово) и куда."""
    lines = Path(path).read_text(encoding="utf-8").lstrip("\ufeff").split("\n")
    if not lines or lines[0] != "\t".join(ZACHALO_FIELDS):
        sys.exit("refused: zachalo fixes: bad header")
    rows = []
    for lineno, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) != len(ZACHALO_FIELDS):
            sys.exit("refused: zachalo fixes line %d: expected %d fields" % (lineno, len(ZACHALO_FIELDS)))
        row = dict(zip(ZACHALO_FIELDS, f))
        for k in ("verse_now", "verse_new"):
            if not VERSE_REF_RE.fullmatch(row[k]):
                sys.exit("refused: zachalo fixes line %d: bad %s %r" % (lineno, k, row[k]))
        rows.append(row)
    return rows


def apply_zachalo_fixes(data, rows):
    """Переносит или перенумеровывает пометки зачал; пометка «откуда» должна найтись ровно одна, иначе отказ."""
    if data.get("kind") != "elis":
        return []
    verses = {}
    for ch in data.get("chapters") or []:
        for v in ch.get("verses") or []:
            if not v.get("heading"):
                verses[(ch["n"], v["n"])] = v
    applied = []
    for row in rows:
        if row["book"] != data.get("book"):
            continue
        a = tuple(int(x) for x in VERSE_REF_RE.fullmatch(row["verse_now"]).groups())
        b = tuple(int(x) for x in VERSE_REF_RE.fullmatch(row["verse_new"]).groups())
        n_now = int(row["n_now"]) if row["n_now"] else None
        n_new = int(row["n_new"]) if row["n_new"] else None
        src, dst = verses.get(a), verses.get(b)
        if src is None or dst is None:
            sys.exit("refused: zachalo fix %s %s: no verse" % (row["book"], row["verse_now"]))
        hit = [z for z in src.get("zachalo") or [] if z.get("n") == n_now and z.get("pos") == int(row["pos_now"])]
        if len(hit) != 1:
            sys.exit("refused: zachalo fix %s %s: %d marks n=%s pos=%s" % (row["book"], row["verse_now"], len(hit), n_now, row["pos_now"]))
        pos = int(row["pos_new"])
        if pos > len(dst.get("tokens") or []):
            sys.exit("refused: zachalo fix %s %s: pos %d past the verse" % (row["book"], row["verse_new"], pos))
        src["zachalo"].remove(hit[0])
        word = re.sub(r"\s*\d+$", "", hit[0]["label"])   # «Заⷱ҇» или «Зача́ло», как в источнике
        z = {"n": n_new, "label": "%s %d" % (word, n_new) if n_new is not None else "Заⷱ҇", "pos": pos}
        dst["zachalo"] = sorted((dst.get("zachalo") or []) + [z], key=lambda x: x["pos"])
        applied.append(row)
    if applied:
        data["zachalo_fixes"] = applied
    return applied


def _git(git_dir, args):
    return subprocess.run(
        ["git", "--git-dir", git_dir] + args,
        capture_output=True,
    )


def main(argv):
    if not argv or argv[0] != "build":
        sys.exit("usage: ponomar_text.py build --git-dir DIR --commit SHA --out DIR --books A,B")

    opts = {}
    i = 1
    while i < len(argv):
        key = argv[i]
        if key not in ("--git-dir", "--commit", "--out", "--books",
                       "--corrections", "--zachalo"):
            sys.exit("unknown option: %s" % key)
        if i + 1 >= len(argv):
            sys.exit("missing value for %s" % key)
        opts[key] = argv[i + 1]
        i += 2

    for key in ("--git-dir", "--commit", "--out", "--books"):
        if key not in opts:
            sys.exit("missing option %s" % key)

    git_dir = opts["--git-dir"]
    r = _git(git_dir, ["rev-parse", opts["--commit"]])
    if r.returncode != 0:
        sys.exit("git rev-parse failed: %s"
                 % r.stderr.decode("utf-8", "replace").strip())
    sha = r.stdout.decode("utf-8", "replace").strip().split()[0]

    books = [book.strip() for book in opts["--books"].split(",")
             if book.strip()]
    out_root = Path(opts["--out"])

    corrections = None
    if "--corrections" in opts:
        corrections = load_bible_corrections(opts["--corrections"])
    applied_by_book = {}
    zfix = load_zachalo_fixes(opts["--zachalo"]) if "--zachalo" in opts else None
    zfix_n = 0

    for book in books:
        for kind, path in (
            ("elis", "Ponomar/languages/cu/bible/elis/%s.text" % book),
            ("synod", "Ponomar/languages/cu/ru/bible/synod/%s.text" % book),
        ):
            r = _git(git_dir, ["show", "%s:%s" % (sha, path)])
            if r.returncode != 0:
                sys.exit("git show failed for %s: %s"
                         % (path, r.stderr.decode("utf-8", "replace").strip()))
            text = r.stdout.decode("utf-8")
            source = {
                "repo": "typiconman/Ponomar",
                "commit": sha,
                "path": path,
                "licence": "GPL-3.0-or-later",
            }
            data = build_book(text, kind, book, source)
            if corrections is not None and kind == "elis":
                applied_by_book[book] = len(
                    apply_bible_corrections(data, corrections))
            if zfix is not None and kind == "elis":
                zfix_n += len(apply_zachalo_fixes(data, zfix))
            out_dir = out_root / kind
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / ("%s.json" % book)).write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            counts = data["counts"]
            print("%s\t%s\t%d\t%d\t%d" % (
                book, kind, counts["chapters"],
                counts["verses"], counts["headings"],
            ))

    if corrections is not None:
        books_set = set(books)
        wanted = {}
        for row in corrections:
            wanted[row["book"]] = wanted.get(row["book"], 0) + 1
        for name, count in wanted.items():
            if name in books_set and applied_by_book.get(name, 0) != count:
                sys.exit("refused corrections for %s: %d of %d applied"
                         % (name, applied_by_book.get(name, 0), count))
        print("corrections\tapplied %d of %d"
              % (sum(applied_by_book.values()), len(corrections)))

    if zfix is not None:
        want = sum(1 for row in zfix if row["book"] in set(books))
        if zfix_n != want:
            sys.exit("refused zachalo fixes: %d of %d applied" % (zfix_n, want))
        print("zachalo\tapplied %d of %d" % (zfix_n, len(zfix)))


if __name__ == "__main__":
    main(sys.argv[1:])
