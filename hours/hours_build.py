#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Словесица (slovesitsa.org)
"""Часы из «Пономаря» (Словесица, 13.11.2026): 1-й, 3-й, 6-й, 9-й, Пасхальные, Царские (три праздника).

Шаблоны languages/xml/Services/*.xml разбираются по правилам Service.java того же коммита:
GET — вложенный шаблон, CREATE — CommonPrayers/<What>.xml, BIBLE — псалом или чтение, GETID — Минея (lives/<Id>.xml),
TITLE/SUBTITLE — Text/<имя>.xml. Условие Cmd считается для чтеца без священника (PS=0), обычного дня (PFlag2=0),
службы целиком (PFlag1=0). Части, зависящие от дня (Var/: тропарь, кондак), не подставляются — только пометка.

Отказ (сборка падает, файл не пишется): неизвестный тег или атрибут; CREATE/GET/GETID/TITLE без своего текста;
псалом или стих чтения, которых нет в нашей сборке; Cmd, который нельзя посчитать или который зависит от дня
недели/дня от Пасхи иначе, чем известные сезонные пометки; пустой текст молитвы не из списка EMPTY_OK.

python3 -I hours_build.py --git-dir ПУТЬ/Ponomar.git --commit 0af645f4 --out ../build/hours
"""
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ponomar_text as pt  # noqa: E402
from kathisma import apply_prayer_corrections, read_tsv, PRAYER_COLUMNS  # noqa: E402

SVC = "Ponomar/languages/xml/Services/%s.xml"
CU = "Ponomar/languages/cu/xml/Services/"
LIVES = "Ponomar/languages/cu/xml/lives/%s.xml"
BIBLE_XML = "Ponomar/languages/xml/bible.xml"

# Службы: (файл, флаги, имя выхода). PFlag — какие Царские часы (0 Рождество, 1 Богоявление, 2 Великая пятница).
BASE = {"PS": 0, "PFlag1": 0, "PFlag2": 0, "PFlag3": 0}
SERVICES = [
    ("Prime", dict(BASE), "1"),
    ("ThirdHour", dict(BASE), "3"),
    ("SixthHour", dict(BASE), "6"),
    ("NinthHour", dict(BASE), "9"),
    ("PaschalHours", dict(BASE), "paskha"),
    ("RoyalHours", dict(BASE, PFlag=0), "tsarskie-rozhdestvo"),
    ("RoyalHours", dict(BASE, PFlag=1), "tsarskie-bogoyavlenie"),
    ("RoyalHours", dict(BASE, PFlag=2), "tsarskie-pyatnica"),
]
# Переменные, от которых служба зависит, но которые мы не выбираем: проверяется, что итог от них не зависит,
# кроме условий из SEASON — их часть показывается с пометкой «когда».
FREE = {"nday": [-100, -48, -3, 0, 1, 39, 40, 45, 49, 50, 60], "dow": list(range(7))}
SEASON = {
    "(nday >= 0 && nday < 40)": "pascha_0_39",
    "(nday < 0 || nday >= 50)": "not_pascha_0_49",
}
# Великий пост (пометкой, без переключателя). В 1-м, 3-м, 6-м, 9-м часах части с PFlag2 берутся все:
# PFlag2 = 0 — обычный день, 1 — пост без кафизмы, 2 — пост с кафизмой (Страстная, 3, — не разбираем). Сезон части —
# по тому, при каких PFlag2 она читается; день недели в пост — будни (1–6): постом часы так читают только по будням.
LENT_TEMPLATES = {"Prime", "ThirdHour", "SixthHour", "NinthHour"}
LENT_SEASON = {
    (True, False, False): "not_lent",
    (False, True, True): "lent",
    (False, False, True): "lent_kathisma",
}
EMPTY_OK = {"Ending", "SmallEndingWP"}  # у Пономаря пустые: отпуст чтеца не набран
VAR_LABEL = {"PTrop": "тропарь дня", "PKont": "кондак дня", "PKath": "кафизма дня"}

ATTRS = {
    "TITLE": {"Value", "Header", "Source", "Comment"},
    "SUBTITLE": {"Value", "Comment"},
    "GET": {"File", "Cmd", "Null"},
    "CREATE": {"What", "Who", "RedFirst", "NewLine", "Times", "Cmd", "Command", "CommandB", "Header"},
    "BIBLE": {"Verses", "getReading", "Who", "RedFirst", "NewLine", "Header", "TwoStars", "Cmd"},
    "GETID": {"Type", "Id", "What", "Who", "RedFirst", "NewLine", "Header", "Times", "Cmd"},
}
CMD_TOKEN = re.compile(r"\s*(\|\||&&|==|!=|>=|<=|[<>()!]|-?\d+|[A-Za-z]\w*)")


def refuse(msg):
    sys.exit("ОТКАЗ: " + msg)


READING_COLUMNS = ["service", "n", "was", "became", "basis", "sheet", "who"]


def apply_reading_fixes(services, rows, bible):
    """Правки отрывков чтений по печати (corrections_readings.tsv): n-е по счёту чтение службы с ref = was
    получает отрезки became (каждый стих проверяет Bible.ranges). Счёт — по refs до правок; отказ, если такого
    вхождения нет или строка повторяет уже применённую."""
    orig = {slug: [p.get("ref") if p["kind"] == "reading" else None for p in s["parts"]] for slug, s in services.items()}
    seen, applied = set(), []
    for r in rows:
        if r["service"] not in services:
            refuse("corrections_readings.tsv: нет службы %s" % r["service"])
        key = (r["service"], r["was"], r["n"])
        if key in seen:
            refuse("corrections_readings.tsv: строка повторена: %s" % (key,))
        seen.add(key)
        idx = [i for i, ref in enumerate(orig[r["service"]]) if ref == r["was"]]
        n = int(r["n"])
        if not 1 <= n <= len(idx):
            refuse("corrections_readings.tsv: в %s чтение %s встречается %d раз, нужно №%d" % (r["service"], r["was"], len(idx), n))
        parts = services[r["service"]]["parts"]
        part = parts[idx[n - 1]]
        bid, rg = bible.ranges(r["became"])
        if bid != part["book"]:  # другая книга — и возглас перед чтением («Ѿ лꙋкѝ…» → «Ѿ матѳе́а…»), иначе он врёт
            intro = next((p for p in reversed(parts[:idx[n - 1]]) if p["kind"] == "intro"), None)
            same = [p for s in services.values() for p in s["parts"] if p["kind"] == "intro" and p.get("book") == bid]
            if not intro or intro.get("book") != part["book"] or not same:
                refuse("corrections_readings.tsv: %s %s → %s: возглас перед чтением не найден" % (r["service"], r["was"], r["became"]))
            intro.update(book=bid, **{k: same[0][k] for k in ("text", "tokens", "lead") if k in same[0]})
        part.update(book=bid, ranges=rg, ref=r["became"], ref_ponomar=r["was"])
        applied.append(r)
    return applied


def nfc(s):
    return unicodedata.normalize("NFC", s)


def clean(s):
    return nfc(" ".join(s.split()))


# --- Cmd: маленький разборщик выражений C (без eval) ---
def cmd_eval(expr, env):
    toks, pos = [], 0
    while pos < len(expr):
        m = CMD_TOKEN.match(expr, pos)
        if not m:
            if expr[pos:].strip() == "":
                break
            refuse("Cmd %r: не разобрать с позиции %d" % (expr, pos))
        toks.append(m.group(1))
        pos = m.end()
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def atom():
        t = take()
        if t == "(":
            v = orx()
            if take() != ")":
                refuse("Cmd %r: нет )" % expr)
            return v
        if t == "!":
            return not atom()
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        if t not in env:
            refuse("Cmd %r: неизвестная переменная %s" % (expr, t))
        return env[t]

    def cmp():
        a = atom()
        while peek() in ("==", "!=", ">=", "<=", "<", ">"):
            op, b = take(), atom()
            a = {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, "<": a < b, ">": a > b}[op]
        return a

    def andx():
        a = cmp()
        while peek() == "&&":
            take()
            b = cmp()
            a = bool(a) and bool(b)
        return a

    def orx():
        a = andx()
        while peek() == "||":
            take()
            b = andx()
            a = bool(a) or bool(b)
        return a

    v = orx()
    if i != len(toks):
        refuse("Cmd %r: лишнее после разбора" % expr)
    return bool(v)


def decide(cmd, flags):
    """(включать?, сезон). Перебор свободных переменных: итог обязан не зависеть от них, кроме SEASON."""
    if cmd is None:
        return True, None
    results = set()
    for nd in FREE["nday"]:
        for dw in FREE["dow"]:
            results.add(cmd_eval(cmd, dict(flags, nday=nd, dow=dw)))
    if len(results) == 1:
        return results.pop(), None
    key = cmd.strip()
    if key in SEASON:
        return True, SEASON[key]
    if "nday == -3" in key:  # только Великий четверг — в обычный день не входит
        return False, None
    refuse("Cmd %r зависит от дня, а сезонной пометки для него нет" % cmd)


def decide_lent(cmd, flags):
    """decide() для часов с великопостными частями: (включать?, сезон)."""
    if cmd is None or "PFlag2" not in cmd:
        return decide(cmd, flags)
    on0, season0 = decide(cmd, dict(flags, PFlag2=0))
    if season0:
        refuse("Cmd %r: пасхальная и великопостная пометка сразу" % cmd)
    res = [on0]
    for v in (1, 2):
        r = {cmd_eval(cmd, dict(flags, PFlag2=v, nday=nd, dow=dw)) for nd in FREE["nday"] for dw in range(1, 7)}
        if len(r) != 1:
            refuse("Cmd %r в пост (PFlag2=%d) зависит от дня" % (cmd, v))
        res.append(r.pop())
    res = tuple(res)
    if res == (True, True, True):
        return True, None
    if res == (False, False, False):
        return False, None
    if res not in LENT_SEASON:
        refuse("Cmd %r: такого сочетания обычный день / пост %r нет в LENT_SEASON" % (cmd, res))
    return True, LENT_SEASON[res]


def merge_times(parts):
    """Обычная часть и та же постом, отличаются только числом раз (богородичны 1-го часа) — одна строка с times_lent."""
    out, i = [], 0
    while i < len(parts):
        j = i
        while j < len(parts) and parts[j].get("season") == "not_lent":
            j += 1
        n = j - i
        y = parts[j:j + n]
        same = n > 0 and len(y) == n and all(
            b.get("season") == "lent" and a["kind"] == b["kind"] == "prayer" and a["what"] == b["what"] and a["who"] == b["who"]
            and a.get("command") == b.get("command") for a, b in zip(parts[i:j], y))
        if same:
            for a, b in zip(parts[i:j], y):
                a = {k: v for k, v in a.items() if k != "season"}
                if b["times"] != a["times"]:
                    a["times_lent"] = b["times"]
                out.append(a)
            i = j + n
        else:
            out.append(parts[i])
            i += 1
    return out


class Src:
    def __init__(self, git_dir, sha):
        self.git_dir, self.sha, self.cache = git_dir, sha, {}

    def get(self, path, must=True):
        if path not in self.cache:
            r = pt._git(self.git_dir, ["show", "%s:%s" % (self.sha, path)])
            self.cache[path] = r.stdout.decode("utf-8").lstrip("﻿") if r.returncode == 0 else None
        if self.cache[path] is None and must:
            refuse("нет файла %s" % path)
        return self.cache[path]

    def xml(self, path):
        try:
            return ET.fromstring(self.get(path))
        except ET.ParseError as e:
            refuse("%s: XML: %s" % (path, e))


def common(src, folder, name, must=True):
    """CommonPrayers/Text/Command: {'header', 'text'}; None, если файла нет и must=False."""
    path = CU + folder + "/" + name + ".xml"
    if src.get(path, must) is None:
        return None
    root = src.xml(path)
    if root.tag != "COMMONPRAYER":
        refuse("%s: корень %s" % (path, root.tag))
    out = {"header": None, "text": None}
    for c in root:
        if c.tag not in ("HEADER", "TEXT") or set(c.attrib) != {"Value"}:
            refuse("%s: неизвестный элемент %s %s" % (path, c.tag, sorted(c.attrib)))
        key = c.tag.lower()
        if out[key] is not None:
            refuse("%s: два %s" % (path, c.tag))
        out[key] = clean(c.attrib["Value"])
    if out["text"] is None:
        refuse("%s: нет TEXT" % path)
    return out


def tok(text):
    lead, tokens, _ = pt._split_text(text)
    return {"text": text, "lead": lead, "tokens": tokens}


class Bible:
    """Наша сборка: Псалтирь и НЗ — ../build/elis, ВЗ — ../ot/build/elis. Только проверка наличия стихов."""

    def __init__(self, intros):
        self.books, self.intros = {}, intros

    def book(self, bid):
        if bid not in self.books:
            for d in (HERE.parent / "build" / "elis", HERE.parent / "ot" / "build" / "elis"):
                f = d / ("%s.json" % bid)
                if f.is_file():
                    data = json.loads(f.read_text(encoding="utf-8"))
                    self.books[bid] = {c["n"]: {v["n"] for v in c["verses"]} for c in data["chapters"]}
                    break
            else:
                refuse("книги %s нет в нашей сборке" % bid)
        return self.books[bid]

    def ranges(self, ref):
        """'Isa_7:10-16,8:1-4,8-10' -> [[7,10,7,16],[8,1,8,4],[8,8,8,10]] (гл., стих, гл., стих); каждый стих проверен."""
        bid, _, spec = ref.rpartition("_")
        chs = self.book(bid)
        out, cur = [], None
        for part in spec.split(","):
            m = re.fullmatch(r"(?:(\d+):)?(\d+)(?:-(?:(\d+):)?(\d+))?", part)
            if not m:
                refuse("Verses %r: не разобрать %r" % (ref, part))
            c1 = int(m.group(1)) if m.group(1) else cur
            if c1 is None:
                refuse("Verses %r: нет главы" % ref)
            v1 = int(m.group(2))
            c2 = int(m.group(3)) if m.group(3) else c1
            v2 = int(m.group(4)) if m.group(4) else v1
            cur = c2
            for c in range(c1, c2 + 1):
                if c not in chs:
                    refuse("Verses %r: нет главы %d в сборке" % (ref, c))
                lo = v1 if c == c1 else 1
                hi = v2 if c == c2 else max(chs[c])
                miss = [v for v in range(lo, hi + 1) if v not in chs[c]]
                if miss:
                    refuse("Verses %r: в сборке нет стихов %d:%s" % (ref, c, miss))
            out.append([c1, v1, c2, v2])
        return bid, out


def build_service(src, name, flags, bible, prayers, menaion, report):
    parts, missing_var = [], []
    counts = {}

    def add(kind, **kw):
        counts[kind] = counts.get(kind, 0) + 1
        parts.append(dict(kind=kind, **kw))

    def run(path, group):
        root = src.xml(path)
        if root.tag not in ("SERVICES", "SERVICE"):
            refuse("%s: корень %s" % (path, root.tag))
        for el in root:
            if el.tag not in ATTRS:
                refuse("%s: неизвестный тег %s" % (path, el.tag))
            extra = set(el.attrib) - ATTRS[el.tag]
            if extra:
                refuse("%s: %s с неизвестными атрибутами %s" % (path, el.tag, sorted(extra)))
            on, season = (decide_lent if name in LENT_TEMPLATES else decide)(el.attrib.get("Cmd"), flags)
            if not on:
                continue
            a = el.attrib
            who = a.get("Who", "")
            if season in ("lent", "lent_kathisma") and who == "P":
                who = ""  # молитва Ефрема в чине без священника — у чтеца (части священника при PS=0 шаблон уже отсёк)
            times = int(a.get("Times", "1"))
            head = a.get("Header") == "1"
            common_kw = {"group": group, "who": who}
            if season:
                common_kw["season"] = season
            if el.tag == "TITLE":
                t = common(src, "Text", a["Value"])["text"]
                s = common(src, "Text", a["Source"])["text"] if "Source" in a else None
                # Comment необязателен и в Service.java (text4 != null): файла может не быть.
                c = common(src, "Text", a["Comment"], must=False) if "Comment" in a else None
                c = c["text"] if c else None
                add("title", text=t, source=s, comment=c)
            elif el.tag == "SUBTITLE":
                add("subtitle", key=a["Value"], text=common(src, "Text", a["Value"])["text"])
            elif el.tag == "GET":
                f = a["File"]
                if f.startswith("Var/"):
                    stem = re.sub(r"\d+$", "", f[4:])
                    label = VAR_LABEL.get(stem)
                    if not label:
                        refuse("%s: GET %s — переменная часть без пометки" % (path, f))
                    exists = src.get(CU + f + ".xml", must=False) is not None
                    if not exists:
                        missing_var.append(f)
                    add("var", var=f, label=label, **common_kw)
                else:
                    run(SVC % f, "beginning" if f == "UsualBeginning" else f)
            elif el.tag == "CREATE":
                w = a["What"]
                if w not in prayers:
                    refuse("%s: CREATE %s — нет CommonPrayers/%s.xml" % (path, w, w))
                p = prayers[w]
                kw = dict(common_kw, what=w, times=times)
                if head and p["header"]:
                    kw["header"] = p["header"]
                for k in ("Command", "CommandB"):
                    if k in a:
                        common(src, "Command", a[k])  # файл указания обязан быть; на сайте — по ключу (Bow, Prostration…)
                        kw["command" if k == "Command" else "command_b"] = a[k]
                if p["text"] == "":
                    if w not in EMPTY_OK:
                        refuse("%s: CREATE %s — пустой текст" % (path, w))
                    add("gap", **kw)
                else:
                    add("prayer", **kw)
            elif el.tag == "BIBLE":
                if "getReading" in a:
                    bid = a["getReading"]
                    if bid not in bible.intros:
                        refuse("%s: getReading %s — нет вступления в bible.xml" % (path, bid))
                    add("intro", book=bid, **common_kw, **tok(bible.intros[bid]))
                    continue
                ref = a["Verses"]
                m = re.fullmatch(r"Psalm_(\d+)", ref)
                if m:
                    n = int(m.group(1))
                    if n not in bible.book("Psalm") or not bible.book("Psalm")[n]:
                        refuse("%s: псалма %d нет в нашей сборке" % (path, n))
                    add("psalm", psalm=n, **common_kw)
                else:
                    bid, rg = bible.ranges(ref)
                    add("reading", book=bid, ranges=rg, ref=ref, **common_kw)
            elif el.tag == "GETID":
                typ = a.get("Type", "M")
                lid = ("98" + a["Id"]) if typ == "T" else a["Id"]
                key = (lid, a["What"])
                if key not in menaion:
                    root2 = src.xml(LIVES % lid)
                    sect, _, typ_attr = a["What"].rpartition("/")
                    node = root2.find("SERVICE")
                    if node is None:
                        refuse("%s: нет SERVICE" % (LIVES % lid))
                    for step in [s for s in sect.split("/") if s][:-1]:
                        node = node.find(step)
                        if node is None:
                            refuse("GETID %s %s: нет раздела %s" % (lid, a["What"], step))
                    leaf = [s for s in sect.split("/") if s][-1]
                    hits = [e for e in node.findall(leaf) if e.attrib.get("Type") == typ_attr]
                    if len(hits) != 1:
                        refuse("GETID %s %s: найдено %d элементов" % (lid, a["What"], len(hits)))
                    e = hits[0]
                    text = clean("".join(e.itertext()))
                    if not text:
                        refuse("GETID %s %s: пустой текст" % (lid, a["What"]))
                    hdr = e.attrib.get("Header")
                    hdr = clean(re.sub(r"<BR>", " · ", hdr)) if hdr else None
                    menaion[key] = dict(tok(text), header=hdr, tone=e.attrib.get("Tone"), lid=lid, path=a["What"])
                    if hdr:
                        menaion[key]["header_tok"] = tok(hdr)
                kw = dict(common_kw, ref="%s:%s" % key, times=times)
                if head and menaion[key]["header"]:
                    kw["header"] = menaion[key]["header"]
                add("menaion", **kw)

    run(SVC % name, "main")
    report.append((name, flags.get("PFlag", ""), counts, missing_var))
    return merge_times(parts) if name in LENT_TEMPLATES else parts


def main(argv):
    o = dict(zip(argv[0::2], argv[1::2]))
    git_dir, commit, out = o["--git-dir"], o["--commit"], Path(o["--out"])
    sha = pt._git(git_dir, ["rev-parse", commit]).stdout.decode().split()[0]
    src = Src(git_dir, sha)

    # Вступления к чтениям — блок cu/bible/elis файла bible.xml (как Bible.getIntro).
    intros = {}
    for blk in ET.fromstring(src.get(BIBLE_XML)).findall("BIBLE"):
        if blk.attrib.get("Id") == "cu/bible/elis":
            intros = {b.attrib["Id"]: clean(b.attrib.get("Intro", "")) for b in blk.findall("BOOK")}
    if not intros:
        refuse("bible.xml: нет блока cu/bible/elis")
    bible = Bible(intros)

    # Все молитвы, упомянутые любым шаблоном при любых флагах: правки prayers.tsv применяются к полному набору.
    names = set()
    for name in sorted({s[0] for s in SERVICES} | {"UsualBeginning"}):
        names |= set(re.findall(r'<CREATE[^>]*What="([^"/]+)"', src.get(SVC % name)))
    prayers, absent = {}, []
    for w in sorted(names):
        p = common(src, "CommonPrayers", w, must=False)
        if p is None:
            absent.append(w)
            continue
        prayers[w] = dict(p, **tok(p["text"]))
        if p["header"]:
            prayers[w]["header_tok"] = tok(p["header"])
    rows = read_tsv(HERE.parent / "corrections" / "prayers.tsv", PRAYER_COLUMNS)
    applied = apply_prayer_corrections(prayers, rows)
    # Свои правки часов (сверка с Часословом 1991 и Цветной Триодью 1992) — после правок prayers.tsv, тем же способом.
    own = read_tsv(HERE / "corrections_hours.tsv", PRAYER_COLUMNS)
    applied_own = apply_prayer_corrections(prayers, [r for r in own if ":" not in r["what"]])

    report, menaion, services = [], {}, {}
    for name, flags, slug in SERVICES:
        services[slug] = {"template": name, "flags": flags, "parts": build_service(src, name, flags, bible, prayers, menaion, report)}

    # Правки текстов Минеи/Триоди: what = "<Id>:<путь>", как ключ в menaion.
    for r in [r for r in own if ":" in r["what"]]:
        m = menaion.get(tuple(r["what"].split(":", 1)))
        if m is None:
            refuse("corrections_hours.tsv: нет текста Минеи %s" % r["what"])
        was, became = nfc(r["was"]), nfc(r["became"])
        if m["text"].count(was) != 1:
            refuse("corrections_hours.tsv: %r встречается %d раз в %s" % (was, m["text"].count(was), r["what"]))
        m.update(tok(nfc(m["text"].replace(was, became, 1))))
        applied_own.append(r)
    # Отрезки чтений Царских часов — по Минее 1997/1996, Триоди 1992 и Типикону (сверка 17.11.2026).
    applied_read = apply_reading_fixes(services, read_tsv(HERE / "corrections_readings.tsv", READING_COLUMNS), bible)
    used =sorted({p["what"] for s in services.values() for p in s["parts"] if p["kind"] in ("prayer", "gap")})
    data = {
        "source": {"repo": "typiconman/Ponomar", "commit": sha, "licence": "GPL-3.0-or-later",
                   "templates": "Ponomar/languages/xml/Services", "texts": CU, "lives": LIVES % "<Id>"},
        "variant": "чтец без священника (PS=0), служба целиком (PFlag1=0); 1-й, 3-й, 6-й, 9-й час — обычный день и Великий пост (PFlag2 0/1/2, части поста с пометкой season), Страстная (PFlag2=3) не разобрана",
        "services": services,
        "prayers": {w: prayers[w] for w in used},
        "menaion": {"%s:%s" % k: v for k, v in menaion.items()},
        "corrections": [dict(r, file="prayers.tsv") for r in applied] + [dict(r, file="hours/corrections_hours.tsv") for r in applied_own]
                       + [dict(what="%s#%s" % (r["service"], r["n"]), was=r["was"], became=r["became"], basis=r["basis"],
                               sheet=r["sheet"], who=r["who"], file="hours/corrections_readings.tsv") for r in applied_read],
        "absent_prayers": absent,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "hours.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    kinds = ["prayer", "psalm", "reading", "intro", "menaion", "var", "gap", "title", "subtitle"]
    lines = ["template\tPFlag\t" + "\t".join(kinds) + "\tvar_files_absent"]
    for name, pf, counts, miss in report:
        lines.append("%s\t%s\t%s\t%s" % (name, pf, "\t".join(str(counts.get(k, 0)) for k in kinds), ",".join(miss)))
    (out / "parts.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("правок corrections_hours.tsv применено %d" % len(applied_own))
    print("правок corrections_readings.tsv применено %d" % len(applied_read))
    print("молитв использовано %d, правок prayers.tsv применено %d, стихир/тропарей Минеи %d; нет в CommonPrayers (не в нашем варианте): %s"
          % (len(used), len(applied), len(menaion), ", ".join(absent) or "—"))


if __name__ == "__main__":
    main(sys.argv[1:])
