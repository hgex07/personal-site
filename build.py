#!/usr/bin/env python3
"""
build.py — turns every works/*.md file into a blog-style HTML page
and fills the two generated lists inside index.html (home teaser + works tab).
index.html is a single tabbed file; there is no separate works.html.

Usage:  python3 build.py   (run before pushing to GitHub Pages)

Security notes:
  * Only *.md files directly inside works/ are considered — no recursion,
    no symlinks (iterdir + is_file check would follow symlinks, so we use
    Path.is_file() combined with a strict filename whitelist below).
  * Filenames must match a strict whitelist; anything else is skipped
    with a warning. Slugs are rebuilt from [a-z0-9-] only, so the output
    path can never escape works/.
  * Front-matter values are HTML-escaped when inserted into templates.
  * Raw HTML inside your markdown body is passed through — that's fine
    because you author these files yourself; don't accept .md files
    from untrusted sources without review.
"""

import html
import re
import sys
from datetime import date
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent
WORKS = ROOT / "works"

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

PAGE = """<!doctype html>
<html lang="en" data-fx="mesh vignette rules hover">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Héctor</title>
  <meta name="description" content="{description}">
  <link rel="stylesheet" href="{root}style.css">
  <script>try{{var p=localStorage.getItem("palette");if(p)document.documentElement.setAttribute("data-palette",p);var t=localStorage.getItem("theme");document.documentElement.setAttribute("data-theme",t==="dark"||t==="light"?t:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"))}}catch(e){{}}</script>
</head>
<body>
  <div class="bg bg-mesh" aria-hidden="true"></div>
  <div class="bg bg-vignette" aria-hidden="true"></div>
  <div class="bg bg-rules" aria-hidden="true"></div>
  <div class="progress" aria-hidden="true"></div>
  <main>
    <header>
      <p class="site-title">HGE's own web corner</p>
      <nav>
        <a href="{root}index.html#/home">home</a>
        <a href="{root}index.html#/works">works</a>
        <span class="controls">
          <span class="palette" role="group" aria-label="accent colour">
            <button class="swatch sw-olive" data-palette="olive" title="olive" aria-label="olive accent"></button>
            <button class="swatch sw-slate" data-palette="slate" title="slate" aria-label="slate accent"></button>
            <button class="swatch sw-clay" data-palette="clay" title="clay" aria-label="clay accent"></button>
            <button class="swatch sw-graphite" data-palette="graphite" title="graphite" aria-label="graphite accent"></button>
          </span>
          <button id="theme-toggle" class="theme-toggle" title="switch theme">◐ auto</button>
        </span>
      </nav>
    </header>

{body}
  </main>

  <footer>
    Too busy studying to put trackers here
  </footer>
  <script>
    (function () {{
      var root = document.documentElement;
      var buttons = document.querySelectorAll(".swatch");
      function mark() {{
        var current = root.getAttribute("data-palette") || "olive";
        buttons.forEach(function (b) {{
          b.setAttribute("aria-pressed", String(b.dataset.palette === current));
        }});
      }}
      buttons.forEach(function (b) {{
        b.addEventListener("click", function () {{
          root.setAttribute("data-palette", b.dataset.palette);
          try {{ localStorage.setItem("palette", b.dataset.palette); }} catch (e) {{}}
          mark();
        }});
      }});
      mark();

      var toggle = document.getElementById("theme-toggle");
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      function mode() {{
        try {{ return localStorage.getItem("theme") || "system"; }} catch (e) {{ return "system"; }}
      }}
      function paint() {{
        var m = mode();
        root.setAttribute("data-theme",
          m === "system" ? (mq.matches ? "dark" : "light") : m);
        toggle.textContent = m === "system" ? "◐ auto" : m === "dark" ? "☾ dark" : "☀ light";
      }}
      toggle.addEventListener("click", function () {{
        var m = mode(), next = m === "system" ? "light" : m === "light" ? "dark" : "system";
        try {{
          if (next === "system") localStorage.removeItem("theme");
          else localStorage.setItem("theme", next);
        }} catch (e) {{}}
        paint();
      }});
      mq.addEventListener("change", function () {{ if (mode() === "system") paint(); }});
      paint();
    }})();
  </script>
</body>
</html>
"""

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
      <a class="backlink" href="../index.html#/works">← back to works</a>
    </article>
"""

# --- build ------------------------------------------------------------------


def collect_posts() -> list[dict]:
    if not WORKS.is_dir():
        sys.exit(f"error: {WORKS} does not exist")

    posts = []
    for path in sorted(WORKS.iterdir()):
        if path.name.startswith("."):
            continue  # ignore dotfiles
        if not path.is_file() or path.is_symlink():
            continue  # never follow symlinks
        if path.suffix.lower() != ".md":
            continue  # generated html and other files live here too
        if not FILENAME_OK.match(path.name):
            print(f"warning: skipping {path.name!r} (filename not whitelisted)")
            continue

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"warning: skipping {path.name!r} (not valid utf-8)")
            continue

        meta, body = parse_frontmatter(raw)
        slug = slugify(path.stem)
        title = meta.get("title") or path.stem.replace("-", " ").replace("_", " ")
        summary = meta.get("summary", "")
        posts.append(
            {
                "slug": slug,
                "title": title,
                "summary": summary,
                "date": parse_date(meta.get("date"), date.fromtimestamp(path.stat().st_mtime)),
                "body_md": body.strip(),
                "source": path.name,
            }
        )

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def render_entries(posts: list[dict]) -> str:
    return "\n".join(
        ENTRY.format(
            href=f"works/{p['slug']}.html",
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


def inject_works(posts: list[dict]) -> None:
    """Fill both generated lists in index.html: the home teaser (newest post)
    and the works tab (every post)."""
    index = ROOT / "index.html"
    text = index.read_text(encoding="utf-8")

    text, ok_latest = replace_block(text, "latest", render_entries(posts[:1]))
    text, ok_all = replace_block(text, "works", render_entries(posts))

    if not ok_latest:
        print("warning: latest markers not found in index.html")
    if not ok_all:
        print("warning: works markers not found in index.html")
    if ok_latest or ok_all:
        index.write_text(text, encoding="utf-8")
        print(f"updated index.html (latest: {min(1, len(posts))}, works tab: {len(posts)} posts)")


def build() -> None:
    md = markdown.Markdown(extensions=["fenced_code", "tables", "smarty"])
    posts = collect_posts()

    # individual post pages -> works/<slug>.html
    for post in posts:
        md.reset()
        content = md.convert(post["body_md"])
        body = POST_BODY.format(
            title=html.escape(post["title"]),
            date=post["date"].isoformat(),
            content=content,
        )
        page = PAGE.format(
            title=html.escape(post["title"]),
            description=html.escape(post["summary"] or post["title"]),
            root="../",
            body=body,
        )
        out = WORKS / f"{post['slug']}.html"
        out.write_text(page, encoding="utf-8")
        print(f"built {out.relative_to(ROOT)}  (from {post['source']})")

    # both lists -> index.html (home teaser + works tab)
    inject_works(posts)

    stale = ROOT / "works.html"
    if stale.exists():
        stale.unlink()
        print("removed works.html  (merged into index.html)")


if __name__ == "__main__":
    build()
