#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Словесица (slovesitsa.org)
"""Кафизмы, «обычное начало» и молитвы (Словесица, 11.11.2026)."""
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I не кладёт папку скрипта в путь
from ponomar_text import _split_text  # noqa: E402

KATHISMA_COLUMNS = [
    "kathisma", "stasis", "was", "became", "basis", "sheet", "who",
]
PRAYER_COLUMNS = ["what", "was", "became", "basis", "sheet", "who"]

PSALM_RE = re.compile(r"Psalm_(\d+)(?::(\d+)-(\d+))?")
PSALM_ITEM_RE = re.compile(r"(\d+)(?::(\d+)-(\d+))?")
DIGITS_RE = re.compile(r"\d+")

KATHISMA_BIBLE_ATTRS = {
    "Verses", "Who", "RedFirst", "NewLine", "Header", "TwoStars",
}
KATHISMA_CREATE_ATTRS = {"What", "Who", "RedFirst", "NewLine", "Times"}
SERVICE_CREATE_ATTRS = {
    "What", "Who", "RedFirst", "NewLine", "Times", "Cmd", "Command",
}


def _parse_xml(xml_text, label):
    if xml_text.startswith("\ufeff"):
        xml_text = xml_text[1:]
    try:
        return ET.fromstring(xml_text)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError("%s: XML parse error: %s" % (label, exc))


def _check_attrs(element, allowed, label):
    extra = sorted(set(element.attrib) - set(allowed))
    if extra:
        raise ValueError("%s: unknown attribute %s"
                         % (label, ", ".join(extra)))


def _parse_times(element, label):
    if "Times" not in element.attrib:
        return 1
    raw = element.attrib["Times"]
    if not DIGITS_RE.fullmatch(raw):
        raise ValueError("%s: bad Times %r" % (label, raw))
    times = int(raw)
    if times < 1:
        raise ValueError("%s: bad Times %r" % (label, raw))
    return times


def _parse_create(element, allowed, label):
    _check_attrs(element, allowed, label)
    if "What" not in element.attrib:
        raise ValueError("%s: missing What" % label)
    return {
        "what": element.attrib["What"],
        "who": element.attrib.get("Who", ""),
        "times": _parse_times(element, label),
    }


def parse_kathisma(xml_text, n):
    root = _parse_xml(xml_text, "Kathisma%s" % n)
    if root.tag != "SERVICES":
        raise ValueError("Kathisma%s: root is not SERVICES" % n)
    _check_attrs(root, set(), "Kathisma%s SERVICES" % n)

    stases = []
    current = None
    after_started = False
    last_kind = None

    for child in root:
        if child.tag == "BIBLE":
            label = "Kathisma%s BIBLE" % n
            _check_attrs(child, KATHISMA_BIBLE_ATTRS, label)
            verses = child.attrib.get("Verses")
            if verses is None:
                raise ValueError("%s: missing Verses" % label)
            match = PSALM_RE.fullmatch(verses)
            if not match:
                raise ValueError("%s: bad Verses %r" % (label, verses))
            psalm = int(match.group(1))
            start = int(match.group(2)) if match.group(2) else None
            end = int(match.group(3)) if match.group(3) else None
            if start is not None and end is not None and start > end:
                raise ValueError("%s: range is backwards" % label)

            if after_started:
                current = None
                after_started = False
            if current is None:
                current = {
                    "n": len(stases) + 1,
                    "psalms": [],
                    "after": [],
                }
                stases.append(current)
            current["psalms"].append({
                "psalm": psalm,
                "from": start,
                "to": end,
            })
            last_kind = "BIBLE"
        elif child.tag == "CREATE":
            label = "Kathisma%s CREATE" % n
            item = _parse_create(child, KATHISMA_CREATE_ATTRS, label)
            if current is None:
                raise ValueError("%s: CREATE before BIBLE" % label)
            current["after"].append(item)
            after_started = True
            last_kind = "CREATE"
        else:
            raise ValueError("Kathisma%s: unknown element %s"
                             % (n, child.tag))

    if last_kind == "BIBLE":
        raise ValueError("Kathisma%s: file ends with BIBLE" % n)
    if len(stases) != 3:
        raise ValueError("Kathisma%s: expected 3 stases, got %d"
                         % (n, len(stases)))
    return {"n": n, "stases": stases}


def parse_service(xml_text):
    root = _parse_xml(xml_text, "UsualBeginning")
    if root.tag != "SERVICES":
        raise ValueError("UsualBeginning: root is not SERVICES")
    _check_attrs(root, set(), "UsualBeginning SERVICES")

    items = []
    for child in root:
        if child.tag != "CREATE":
            raise ValueError("UsualBeginning: unknown element %s"
                             % child.tag)
        label = "UsualBeginning CREATE"
        item = _parse_create(child, SERVICE_CREATE_ATTRS, label)
        cmd = child.attrib.get("Cmd")
        if cmd is None:
            reader, season = True, None
        elif cmd == "PS != 0":
            reader, season = False, None
        elif cmd == "PS == 0":
            reader, season = True, None
        elif cmd == "(nday >= 0 && nday < 40)":
            reader, season = True, "pascha_0_39"
        elif cmd == "(nday < 0 || nday >= 50)":
            reader, season = True, "not_pascha_0_49"
        else:
            raise ValueError("%s: unknown Cmd %r" % (label, cmd))
        item["cmd"] = cmd
        item["command"] = child.attrib.get("Command")
        item["reader"] = reader
        item["season"] = season
        items.append(item)
    return items


def parse_prayer(xml_text):
    root = _parse_xml(xml_text, "CommonPrayer")
    if root.tag != "COMMONPRAYER":
        raise ValueError("CommonPrayer: root is not COMMONPRAYER")
    _check_attrs(root, set(), "COMMONPRAYER")
    children = list(root)
    if len(children) != 1 or children[0].tag != "TEXT":
        raise ValueError("CommonPrayer: expected exactly one TEXT")
    text_element = children[0]
    _check_attrs(text_element, {"Value"}, "CommonPrayer TEXT")
    if "Value" not in text_element.attrib:
        raise ValueError("CommonPrayer TEXT: missing Value")
    text = unicodedata.normalize("NFC", text_element.attrib["Value"]).strip()
    lead, tokens, _ = _split_text(text)
    return {"text": text, "lead": lead, "tokens": tokens}


def read_tsv(path, columns):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("%s: invalid UTF-8: %s" % (path, exc))
    lines = raw.splitlines()
    if not lines:
        raise ValueError("%s: empty file" % path)
    header = "\t".join(columns)
    if lines[0] != header:
        raise ValueError("%s: bad header: expected %r, got %r"
                         % (path, header, lines[0]))
    rows = []
    for lineno, line in enumerate(lines[1:], 2):
        if line == "":
            continue
        fields = line.split("\t")
        if len(fields) != len(columns):
            raise ValueError("%s:%d: expected %d fields, got %d"
                             % (path, lineno, len(columns), len(fields)))
        rows.append(dict(zip(columns, fields)))
    return rows


def _int_field(row, name, label):
    raw = row.get(name)
    if raw is None:
        raise ValueError("%s: missing %s" % (label, name))
    try:
        return int(raw)
    except ValueError:
        raise ValueError("%s: %s is not integer: %r"
                         % (label, name, raw))


def _parse_psalm_items(text, label):
    if not text:
        raise ValueError("%s: empty psalm list" % label)
    items = []
    for part in text.split(" "):
        match = PSALM_ITEM_RE.fullmatch(part)
        if not match:
            raise ValueError("%s: bad psalm %r" % (label, part))
        psalm = int(match.group(1))
        start = int(match.group(2)) if match.group(2) else None
        end = int(match.group(3)) if match.group(3) else None
        if start is not None and end is not None and start > end:
            raise ValueError("%s: range is backwards in %r"
                             % (label, part))
        items.append({"psalm": psalm, "from": start, "to": end})
    return items


def _format_psalms(psalms):
    parts = []
    for psalm in psalms:
        start = psalm.get("from")
        end = psalm.get("to")
        if start is None and end is None:
            parts.append(str(psalm["psalm"]))
        elif start is not None and end is not None:
            parts.append("%d:%d-%d" % (psalm["psalm"], start, end))
        else:
            raise ValueError("bad psalm entry: %r" % (psalm,))
    return " ".join(parts)


def apply_kathisma_corrections(kathismas, rows):
    by_number = {kathisma["n"]: kathisma for kathisma in kathismas}
    seen = set()
    applied = []
    for row in rows:
        label = "kathisma.tsv"
        kathisma_n = _int_field(row, "kathisma", label)
        stasis_n = _int_field(row, "stasis", label)
        key = (kathisma_n, stasis_n)
        if key in seen:
            raise ValueError("%s: two rows for kathisma %d stasis %d"
                             % (label, kathisma_n, stasis_n))
        seen.add(key)

        kathisma = by_number.get(kathisma_n)
        if kathisma is None:
            raise ValueError("%s: no kathisma %d" % (label, kathisma_n))
        stasis = None
        for candidate in kathisma["stases"]:
            if candidate["n"] == stasis_n:
                stasis = candidate
                break
        if stasis is None:
            raise ValueError("%s: no stasis %d in kathisma %d"
                             % (label, stasis_n, kathisma_n))

        for column in ("basis", "sheet", "who"):
            if not row.get(column):
                raise ValueError("%s: empty %s" % (label, column))

        was = row.get("was")
        if was is None:
            raise ValueError("%s: missing was" % label)
        current = _format_psalms(stasis["psalms"])
        if current != was:
            raise ValueError(
                "%s: kathisma %d stasis %d: was %r, actual %r"
                % (label, kathisma_n, stasis_n, was, current)
            )

        stasis["psalms"] = _parse_psalm_items(
            row.get("became", ""),
            "%s: kathisma %d stasis %d became" % (label, kathisma_n, stasis_n),
        )
        applied.append(row)
    return applied


def apply_prayer_corrections(prayers, rows):
    applied = []
    for row in rows:
        label = "prayers.tsv"
        what = row.get("what")
        if what not in prayers:
            raise ValueError("%s: no prayer %r" % (label, what))
        for column in ("basis", "sheet", "who"):
            if not row.get(column):
                raise ValueError("%s: empty %s" % (label, column))

        was = unicodedata.normalize("NFC", row.get("was", ""))
        became = unicodedata.normalize("NFC", row.get("became", ""))
        text = prayers[what]["text"]
        count = text.count(was)
        if count != 1:
            raise ValueError("%s: %r occurs %d times in %s"
                             % (label, was, count, what))
        new_text = unicodedata.normalize("NFC", text.replace(was, became, 1))
        lead, tokens, _ = _split_text(new_text)
        prayers[what]["text"] = new_text
        prayers[what]["lead"] = lead
        prayers[what]["tokens"] = tokens
        applied.append(row)
    return applied


def verify_cover(kathismas, psalm_verses):
    errors = []
    entries = []
    for kathisma in kathismas:
        for stasis in kathisma.get("stases", []):
            entries.extend(stasis.get("psalms", []))

    previous = None
    for psalm in entries:
        number = psalm["psalm"]
        if previous is not None and number < previous:
            errors.append("psalm %d after %d" % (number, previous))
        previous = number

        if number not in psalm_verses:
            errors.append("unknown psalm %d" % number)
            continue
        if number == 151:
            errors.append("psalm 151 is not allowed")
            continue

        start = psalm.get("from")
        end = psalm.get("to")
        if start is None and end is None:
            continue
        if start is None or end is None:
            errors.append("psalm %d: incomplete range" % number)
            continue
        verses = psalm_verses[number]
        if start not in verses:
            errors.append("psalm %d: unknown verse %d" % (number, start))
        if end not in verses:
            errors.append("psalm %d: unknown verse %d" % (number, end))
        if start > end:
            errors.append("psalm %d: range is backwards" % number)

    grouped = {}
    for psalm in entries:
        number = psalm["psalm"]
        if number not in psalm_verses or number == 151:
            continue
        grouped.setdefault(number, []).append(psalm)

    for number, verses in psalm_verses.items():
        if number == 151:
            continue
        group = grouped.get(number)
        if not group:
            errors.append("psalm %d is not covered" % number)
            continue

        whole = [
            psalm for psalm in group
            if psalm.get("from") is None and psalm.get("to") is None
        ]
        parts = [
            psalm for psalm in group
            if not (psalm.get("from") is None and psalm.get("to") is None)
        ]
        if whole and parts:
            errors.append("psalm %d: whole and parts at once" % number)
            continue
        if len(whole) > 1:
            errors.append("psalm %d: covered more than once" % number)
            continue
        if whole:
            continue
        if len(parts) < 2:
            errors.append("psalm %d: not covered by whole or parts" % number)
            continue
        if not verses:
            errors.append("psalm %d: no verses" % number)
            continue
        if parts[0].get("from") != verses[0]:
            errors.append("psalm %d: first part does not start at first verse"
                          % number)
        if parts[-1].get("to") != verses[-1]:
            errors.append("psalm %d: last part does not end at last verse"
                          % number)
        for left, right in zip(parts, parts[1:]):
            try:
                index = verses.index(left.get("to"))
            except ValueError:
                index = None
            if (index is None or index + 1 >= len(verses)
                    or right.get("from") != verses[index + 1]):
                errors.append("psalm %d: parts are not consecutive" % number)
                break

    if errors:
        raise ValueError("; ".join(errors))


def build(get_text, psalm, corrections_dir, source):
    paths = []

    def read(path):
        try:
            text = get_text(path)
        except FileNotFoundError:
            raise ValueError("missing file: %s" % path)
        paths.append(path)
        return text

    kathismas = []
    for number in range(1, 21):
        path = "Ponomar/languages/xml/Services/Kathisma%d.xml" % number
        text = read(path)
        try:
            kathismas.append(parse_kathisma(text, number))
        except ValueError as exc:
            raise ValueError("%s: %s" % (path, exc))

    corrections_dir = Path(corrections_dir)
    kathisma_tsv = corrections_dir / "kathisma.tsv"
    prayers_tsv = corrections_dir / "prayers.tsv"
    if not kathisma_tsv.is_file() and not prayers_tsv.is_file():
        raise ValueError("no corrections files: kathisma.tsv and prayers.tsv")

    corrections = []
    if kathisma_tsv.is_file():
        rows = read_tsv(kathisma_tsv, KATHISMA_COLUMNS)
        applied = apply_kathisma_corrections(kathismas, rows)
        corrections.extend(
            dict(row, file="kathisma.tsv") for row in applied
        )

    psalm_verses = {
        chapter["n"]: [verse["n"] for verse in chapter["verses"]]
        for chapter in psalm["chapters"]
    }
    verify_cover(kathismas, psalm_verses)

    usual_path = "Ponomar/languages/xml/Services/UsualBeginning.xml"
    usual_text = read(usual_path)
    try:
        usual_beginning = parse_service(usual_text)
    except ValueError as exc:
        raise ValueError("%s: %s" % (usual_path, exc))

    whats = []
    seen = set()

    def add_what(what):
        if what not in seen:
            seen.add(what)
            whats.append(what)

    for item in usual_beginning:
        add_what(item["what"])
    for kathisma in kathismas:
        for stasis in kathisma["stases"]:
            for item in stasis["after"]:
                add_what(item["what"])

    prayers = {}
    for what in whats:
        path = ("Ponomar/languages/cu/xml/Services/CommonPrayers/%s.xml"
                % what)
        text = read(path)
        try:
            prayer = parse_prayer(text)
        except ValueError as exc:
            raise ValueError("%s: %s" % (path, exc))
        prayer["path"] = path
        prayers[what] = prayer

    if prayers_tsv.is_file():
        rows = read_tsv(prayers_tsv, PRAYER_COLUMNS)
        applied = apply_prayer_corrections(prayers, rows)
        corrections.extend(
            dict(row, file="prayers.tsv") for row in applied
        )

    return {
        "source": dict(source, paths=paths),
        "usual_beginning": usual_beginning,
        "kathismas": kathismas,
        "prayers": prayers,
        "corrections": corrections,
    }


def _git(git_dir, args):
    return subprocess.run(
        ["git", "--git-dir", git_dir] + args,
        capture_output=True,
    )


def main(argv):
    if not argv or argv[0] != "build":
        sys.exit("usage: kathisma.py build --git-dir DIR --commit SHA "
                 "--psalm-json FILE --corrections DIR --out FILE")

    allowed = ("--git-dir", "--commit", "--psalm-json",
               "--corrections", "--out")
    opts = {}
    i = 1
    while i < len(argv):
        key = argv[i]
        if key not in allowed:
            sys.exit("unknown option: %s" % key)
        if i + 1 >= len(argv):
            sys.exit("missing value for %s" % key)
        opts[key] = argv[i + 1]
        i += 2

    for key in allowed:
        if key not in opts:
            sys.exit("missing option %s" % key)

    git_dir = opts["--git-dir"]
    try:
        result = _git(git_dir, ["rev-parse", opts["--commit"]])
        if result.returncode != 0:
            raise ValueError("git rev-parse failed: %s"
                             % result.stderr.decode("utf-8", "replace").strip())
        sha = result.stdout.decode("utf-8", "replace").strip().split()[0]

        try:
            psalm = json.loads(
                Path(opts["--psalm-json"]).read_text(encoding="utf-8")
            )
            psalm_verses = {
                chapter["n"]: [verse["n"] for verse in chapter["verses"]]
                for chapter in psalm["chapters"]
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("bad psalm json: %s" % exc)

        def get_text(path):
            shown = _git(git_dir, ["show", "%s:%s" % (sha, path)])
            if shown.returncode != 0:
                raise FileNotFoundError(path)
            return shown.stdout.decode("utf-8")

        source = {
            "repo": "typiconman/Ponomar",
            "commit": sha,
            "licence": "GPL-3.0-or-later",
        }
        data = build(get_text, psalm, opts["--corrections"], source)
    except ValueError as exc:
        sys.exit("ОТКАЗ: %s" % exc)

    out_path = Path(opts["--out"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    stases = sum(len(kathisma["stases"]) for kathisma in data["kathismas"])
    psalms = sum(
        len(stasis["psalms"])
        for kathisma in data["kathismas"]
        for stasis in kathisma["stases"]
    )
    print("%d\t%d\t%d\t%d\t%d" % (
        len(data["kathismas"]), stases, psalms,
        len(data["corrections"]), len(data["prayers"]),
    ))


if __name__ == "__main__":
    main(sys.argv[1:])
