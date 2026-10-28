#!/usr/bin/env python3
"""
build.py — turns every works/*.md and blog/*.md file into an HTML page
and fills the generated lists inside index.html (home teaser + works tab
+ blog tab). index.html is a single tabbed file; there is no separate
works.html or blog.html.

Usage:  python3 build.py   (run before pushing to GitHub Pages)

Security notes:
  * Only *.md files directly inside works/ or blog/ are considered — no recursion,
    no symlinks (iterdir + is_file check would follow symlinks, so we use
    Path.is_file() combined with a strict filename whitelist below).
  * Filenames must match a strict whitelist; anything else is skipped
    with a warning. Slugs are rebuilt from [a-z0-9-] only, so the output
    path can never escape works/.
  * Front-matter values are HTML-escaped when inserted into templates.
  * Raw HTML inside your markdown body is passed through — that's fine
    because you author these files yourself; don't accept .md files
    from untrusted sources without review.
  * Output paths are built from the sanitized slug inside the section
    directory, so they can never escape works/ or blog/.
"""

import html
import re
import sys
from datetime import date
from pathlib import Path

import markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

ROOT = Path(__file__).resolve().parent
SECTIONS = {name: ROOT / name for name in ("works", "blog")}

# --- obsidian-style callouts -------------------------------------------------
# Turns `> [!note] Title` blockquotes into python-markdown admonitions
# (`!!!` static, `???`/`???+` foldable via the `-`/`+` suffix), so the
# `admonition` + `pymdownx.details` extensions can render them.

CALLOUT_RE = re.compile(r"^>\s*\[!([A-Za-z0-9_-]+)\]([-+]?)\s*(.*)$")
QUOTE_RE = re.compile(r"^>\s?(.*)$")

# obsidian aliases -> admonition stock types
CALLOUT_ALIASES = {
    "summary": "abstract",
    "tldr": "abstract",
    "hint": "tip",
    "important": "tip",
    "check": "success",
    "done": "success",
    "help": "question",
    "faq": "question",
    "caution": "warning",
    "attention": "warning",
    "fail": "failure",
    "missing": "failure",
    "error": "danger",
    "cite": "quote",
}


class ObsidianCallouts(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(lines):
            m = CALLOUT_RE.match(lines[i])
            if not m:
                out.append(lines[i])
                i += 1
                continue
            kind = m.group(1).lower()
            kind = CALLOUT_ALIASES.get(kind, kind)
            fold = m.group(2)
            title = m.group(3).strip().replace('"', "&quot;")
            i += 1
            # gather the rest of the blockquote
            body: list[str] = []
            while i < len(lines):
                qm = QUOTE_RE.match(lines[i])
                if not qm:
                    break
                body.append(qm.group(1))
                i += 1
            marker = {"": "!!!", "-": "???", "+": "???+"}[fold]
            head = f"{marker} {kind}"
            if title:
                head += f' "{title}"'
            out.append(head)
            out.extend(f"    {ln}" if ln.strip() else "" for ln in body)
            out.append("")  # blank line so the block terminates
        return out


class ObsidianCalloutExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.preprocessors.register(ObsidianCallouts(md), "obsidian_callouts", 105)

# --- safety: filename whitelist & slug rules -------------------------------

FILENAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
SLUG_OK = re.compile(r"[^a-z0-9]+")
DATE_OK = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def slugify(stem: str) -> str:
    slug = SLUG_OK.sub("-", stem.lower()).strip("-")
    return slug[:80] or "untitled"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal 'key: value' front matter between --- fences."""
    meta: dict[str, str] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = value.strip()
            text = parts[2]
    return meta, text


def parse_date(raw: str | None, fallback: date) -> date:
    if raw and (m := DATE_OK.match(raw)):
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    return fallback


# --- templates --------------------------------------------------------------

# Post pages reuse the chrome (head assets, background layers, header/nav,
# footer, controls script) from index.html itself: at build time we extract
# the <!-- chrome:x --> … <!-- /chrome:x --> blocks and localize relative
# URLs for pages one level deep (works/, blog/). Edit chrome in index.html
# only — just keep the markers intact.

CHROME_RE = re.compile(r"<!-- chrome:([a-z]+) -->(.*?)<!-- /chrome:\1 -->", re.DOTALL)
CHROME_PARTS = ("head", "bg", "header", "footer", "controls")
REL_ATTR_RE = re.compile(r"""\b(href|src)="(?!https?:|mailto:|tel:|#|/)([^"]+)\"""")

PAGE = """<!doctype html>
<html lang="en" data-fx="mesh vignette rules hover">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Héctor</title>
  <meta name="description" content="{description}">
{head}
</head>
<body>
{bg}
  <main>
{header}

{body}
  </main>

{footer}
{controls}
</body>
</html>
"""


def extract_chrome(index_text: str) -> dict[str, str]:
    """Pull the shared chrome blocks out of index.html (single source of truth)."""
    chrome = {
        m.group(1): "\n".join(
            line.rstrip() for line in m.group(2).splitlines()
        ).strip("\n")
        for m in CHROME_RE.finditer(index_text)
    }
    missing = [part for part in CHROME_PARTS if part not in chrome]
    if missing:
        sys.exit(f"error: chrome markers missing in index.html: {', '.join(missing)}")
    return chrome


def localize(fragment: str, root: str = "../") -> str:
    """Rewrite a chrome fragment for a page inside works/ or blog/."""
    frag = REL_ATTR_RE.sub(lambda m: f'{m.group(1)}="{root}{m.group(2)}"', fragment)
    frag = frag.replace('href="#/', f'href="{root}index.html#/')  # tab links
    frag = re.sub(r'\s+(aria-current="page"|data-route="[a-z]+")', "", frag)  # tab state is index-only
    return frag

ENTRY = """      <li>
        <a class="title" href="{href}">{title}</a>
        <span class="meta">{date}</span>{summary}
      </li>"""

POST_BODY = """    <article class="post">
      <header>
        <h1>{title}</h1>
        <p class="meta muted">{date}</p>
      </header>
{content}
      <a class="backlink" href="../index.html#{anchor}">← back to {section}</a>
    </article>
"""

# --- build ------------------------------------------------------------------


def collect_posts(directory: Path) -> list[dict]:
    if not directory.is_dir():
        directory.mkdir(parents=True)
        print(f"created {directory.relative_to(ROOT)}/ (empty)")
        return []

    posts = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue  # ignore dotfiles
        if not path.is_file() or path.is_symlink():
            continue  # never follow symlinks
        if path.suffix.lower() != ".md":
            continue  # generated html and other files live here too
        # if not FILENAME_OK.match(path.name):
        #     print(f"warning: skipping {path.name!r} (filename not whitelisted)")
        #     continue

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"warning: skipping {path.name!r} (not valid utf-8)")
            continue

        meta, body = parse_frontmatter(raw)
        slug = slugify(path.stem)
        title = meta.get("title") or path.stem.replace("-", " ").replace("_", " ")
        posts.append(
            {
                "slug": slug,
                "title": title,
                "summary": meta.get("summary", ""),
                "date": parse_date(meta.get("date"), date.fromtimestamp(path.stat().st_mtime)),
                "body_md": body.strip(),
                "source": path.name,
            }
        )

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def render_entries(posts: list[dict], section: str) -> str:
    return "\n".join(
        ENTRY.format(
            href=f"{section}/{p['slug']}.html",
            title=html.escape(p["title"]),
            date=p["date"].isoformat(),
            summary=(
                f'\n        <p class="summary">{html.escape(p["summary"])}</p>'
                if p["summary"]
                else ""
            ),
        )
        for p in posts
    ) or '      <li><span class="meta">nothing here yet</span></li>'


def replace_block(text: str, marker: str, entries: str) -> tuple[str, bool]:
    """Swap the <!-- marker:start --> … <!-- marker:end --> block in text."""
    block = f"""<!-- {marker}:start -->
    <ul class="works">
{entries}
    </ul>
    <!-- {marker}:end -->"""
    new_text, count = re.subn(
        rf"<!-- {marker}:start -->.*?<!-- {marker}:end -->",
        lambda _: block,
        text,
        flags=re.DOTALL,
    )
    return new_text, bool(count)


def inject_index(works_posts: list[dict], blog_posts: list[dict]) -> None:
    """Fill the generated lists in index.html: the home teaser (newest work),
    the works tab (every work) and the blog tab (every post)."""
    index = ROOT / "index.html"
    text = index.read_text(encoding="utf-8")

    lists = (
        ("latest", works_posts[:1], "works"),
        ("works", works_posts, "works"),
        ("blog", blog_posts, "blog"),
    )
    changed = False
    for marker, posts, section in lists:
        text, ok = replace_block(text, marker, render_entries(posts, section))
        changed |= ok
        if not ok:
            print(f"warning: {marker} markers not found in index.html")
    if changed:
        index.write_text(text, encoding="utf-8")
        print(
            f"updated index.html (latest: {min(1, len(works_posts))}, "
            f"works tab: {len(works_posts)}, blog tab: {len(blog_posts)} posts)"
        )


def build() -> None:
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "smarty",
            "pymdownx.tilde",  # ~~strikethrough~~
            "admonition",
            "pymdownx.details",  # foldable callouts
            ObsidianCalloutExtension(),  # > [!note] syntax
        ]
    )
    all_posts = {section: collect_posts(directory) for section, directory in SECTIONS.items()}

    # shared chrome comes from index.html — the single source of truth
    chrome = {
        part: localize(fragment)
        for part, fragment in extract_chrome((ROOT / "index.html").read_text(encoding="utf-8")).items()
    }

    # individual post pages -> <section>/<slug>.html
    for section, posts in all_posts.items():
        for post in posts:
            md.reset()
            content = md.convert(post["body_md"])
            body = POST_BODY.format(
                title=html.escape(post["title"]),
                date=post["date"].isoformat(),
                content=content,
                anchor=f"/{section}",
                section=section,
            )
            page = PAGE.format(
                title=html.escape(post["title"]),
                description=html.escape(post["summary"] or post["title"]),
                body=body,
                **chrome,
            )
            out = SECTIONS[section] / f"{post['slug']}.html"
            out.write_text(page, encoding="utf-8")
            print(f"built {out.relative_to(ROOT)}  (from {post['source']})")

    # generated lists -> index.html (home teaser + works tab + blog tab)
    inject_index(all_posts["works"], all_posts["blog"])

    for stale_name in ("works.html", "blog.html"):
        stale = ROOT / stale_name
        if stale.exists():
            stale.unlink()
            print(f"removed {stale_name}  (merged into index.html)")


if __name__ == "__main__":
    build()
