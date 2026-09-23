#!/usr/bin/env python3
"""Start Second Brain — проверка базы знаний (только стандартная библиотека Python 3.9+).

  python3 brain_check.py inventory <папка>   # режим Б: файлы, дубли, похожие имена, цены
  python3 brain_check.py check <папка>       # после сборки: ссылки, карта, шапки, входной файл

Скрипт ничего не меняет в папке, только читает. Код выхода check: 0 — ошибок нет, 1 — есть ошибки.
"""
import hashlib
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

TEXT_EXT = {".md", ".txt", ".markdown"}
DOC_EXT = TEXT_EXT | {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".odt", ".rtf"}
ENTRY_FILES = [".hermes.md", "HERMES.md", "AGENTS.override.md", "AGENTS.md", "agents.md", "CLAUDE.md", "claude.md"]
SKIP_DIRS = {".git", ".obsidian", "node_modules", "__pycache__", ".trash"}
SERVICE = {"INDEX.md", "RULES.md", "log.md", "README.md"}
REQUIRED_FIELDS = ("status", "source", "confirmed_by", "checked")
WIKI_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MONEY_RE = re.compile(r"\d[\d\s.,]*\s*(?:₽|руб|р\.|тыс|млн|k\b|к\b|\$|€|usd|eur)", re.I)
START, END = "<!-- second-brain:start -->", "<!-- second-brain:end -->"


def files_in(base):
    for p in sorted(base.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(base).parts):
            yield p


def rel(base, p):
    return p.relative_to(base).as_posix()


def read(p):
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="cp1251", errors="replace")


def norm_stem(name):
    s = unicodedata.normalize("NFKC", Path(name).stem).lower()
    s = re.sub(r"(copy|копия|final|финал|old|стар\w*|нов\w*|new|v\d+|\(\d+\)|\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d+)", " ", s)
    return re.sub(r"[\s_\-.]+", " ", s).strip()


def inventory(base):
    files = list(files_in(base))
    print(f"# Инвентаризация: {base}\nВсего файлов: {len(files)}\n")
    by_hash, by_stem = defaultdict(list), defaultdict(list)
    for p in files:
        by_hash[hashlib.sha256(p.read_bytes()).hexdigest()].append(rel(base, p))
        if p.suffix.lower() in DOC_EXT:
            by_stem[norm_stem(p.name)].append(rel(base, p))
    print("## Файлы")
    for p in files:
        print(f"- {rel(base, p)} ({p.stat().st_size} байт)")
    dups = [v for v in by_hash.values() if len(v) > 1]
    print("\n## Точные дубли (одинаковое содержимое)")
    print("\n".join("- " + " = ".join(v) for v in dups) or "- нет")
    similar = [v for k, v in by_stem.items() if k and len(v) > 1]
    print("\n## Похожие имена (возможные версии одного документа)")
    print("\n".join("- " + ", ".join(v) for v in similar) or "- нет")
    print("\n## Строки с суммами/ценами (искать противоречия)")
    for p in files:
        if p.suffix.lower() in TEXT_EXT:
            hits = [(i, l.strip()) for i, l in enumerate(read(p).splitlines(), 1) if MONEY_RE.search(l)]
            for i, line in hits[:15]:
                print(f"- {rel(base, p)}:{i}: {line[:160]}")
    return 0


def frontmatter(text):
    if not text.startswith("---"):
        return None
    m = re.search(r"\n---\s*(\n|$)", text[3:])
    if not m:
        return None
    fm = {}
    for line in text[3:3 + m.start()].splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.split(" #")[0].strip()
    return fm


def strip_code(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def resolve(base, src, target, by_name):
    t = target.strip().rstrip("/")
    for cand in (base / t, base / (t + ".md"), src.parent / t, src.parent / (t + ".md")):
        if cand.is_file():
            return True
    return len(by_name.get(Path(t).name.lower(), [])) == 1 or len(by_name.get((Path(t).name + ".md").lower(), [])) == 1


def git_root(p):
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def hermes_entry(base):
    """Какой файл проекта Hermes загрузит, если запустить его в этой папке (порядок из документации Context Files)."""
    root = git_root(base)
    chain = [base] if root is None else [base, *[d for d in base.parents if d.is_relative_to(root)]]
    for d in chain:
        for n in (".hermes.md", "HERMES.md"):
            if (d / n).is_file() and read(d / n).strip():
                return d / n
    for n in ("AGENTS.override.md", "AGENTS.md", "agents.md", "CLAUDE.md", "claude.md"):
        if (base / n).is_file() and read(base / n).strip():
            return base / n
    return None


def check(base):
    errors, warnings = [], []
    md = [p for p in files_in(base) if p.suffix.lower() == ".md"]
    by_name = defaultdict(list)
    for p in md:
        by_name[p.name.lower()].append(p)
    index = base / "INDEX.md"
    if not index.is_file():
        errors.append("нет INDEX.md в корне базы")
    for n in ("RULES.md", "log.md"):
        if not (base / n).is_file():
            errors.append(f"нет {n}")
    linked_from_index = set()
    if index.is_file():
        for t in WIKI_RE.findall(strip_code(read(index))):
            for cand in (base / t.strip(), base / (t.strip() + ".md")):
                if cand.is_file():
                    linked_from_index.add(cand.resolve())
        if len(read(index)) > 12000:
            warnings.append("INDEX.md длиннее 12000 знаков — похоже на свалку, а не на карту")
    for p in md:
        r = rel(base, p)
        if p.name in ENTRY_FILES:
            continue
        text = read(p)
        for t in WIKI_RE.findall(strip_code(text)):
            if not resolve(base, p, t, by_name):
                errors.append(f"битая вики-ссылка в {r}: [[{t}]]")
        top = r.split("/", 1)[0]
        if top in ("archive", "sources") or p.name in SERVICE:
            continue
        if p.resolve() not in linked_from_index:
            errors.append(f"{r} не упомянут в INDEX.md")
        fm = frontmatter(text)
        if fm is None:
            errors.append(f"{r}: нет шапки (status/source/confirmed_by/checked)")
        else:
            miss = [k for k in REQUIRED_FIELDS if not fm.get(k)]
            if miss:
                errors.append(f"{r}: в шапке пусто: {', '.join(miss)}")
            if fm.get("status") == "устарел":
                warnings.append(f"{r}: status=устарел вне archive/")
    blocks = []
    for n in ENTRY_FILES:
        f = base / n
        if f.is_file():
            c = read(f).count(START)
            if c:
                blocks.append((n, c))
            if c > 1:
                errors.append(f"{n}: блок second-brain повторяется {c} раз")
            if c != read(f).count(END):
                errors.append(f"{n}: не совпадает число маркеров начала и конца блока")
    entry = hermes_entry(base)
    print(f"# Проверка: {base}\nMarkdown-файлов: {len(md)}")
    print(f"Hermes, запущенный в этой папке, загрузит: {entry.name if entry else 'ничего (входного файла нет)'}")
    print("Блоки second-brain: " + (", ".join(f"{n}×{c}" for n, c in blocks) or "нет"))
    if entry is None or read(entry).count(START) == 0:
        errors.append("входной файл, который загрузит Hermes, не содержит блок second-brain")
    for w in warnings:
        print("ПРЕДУПРЕЖДЕНИЕ:", w)
    for e in errors:
        print("ОШИБКА:", e)
    print("Итог:", "ошибок нет" if not errors else f"ошибок: {len(errors)}")
    return 1 if errors else 0


def main(argv):
    if len(argv) != 3 or argv[1] not in ("inventory", "check"):
        print(__doc__)
        return 2
    base = Path(argv[2]).expanduser().resolve()
    if not base.is_dir():
        print(f"нет папки: {base}")
        return 2
    return inventory(base) if argv[1] == "inventory" else check(base)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
