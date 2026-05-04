
#!/usr/bin/env python3
"""
Letterboxd Watchlist Random Movie Picker
Scrapes the user's public Letterboxd watchlist, applies optional filters,
and returns a random movie with full details.
"""

import os, sys, json, random, re, time
import urllib.request, urllib.error
from html.parser import HTMLParser

# ── Inputs from env ──────────────────────────────────────────────────────────
USERNAME       = os.environ.get("LB_USERNAME", "").strip().lower()
GENRE_FILTER   = os.environ.get("LB_GENRE", "").strip().lower()       # e.g. "horror"
MIN_RATING     = float(os.environ.get("LB_MIN_RATING", "0") or "0")   # 0-5
RUNTIME_FILTER = os.environ.get("LB_RUNTIME", "any").strip().lower()  # any|short|feature

if not USERNAME:
    print(json.dumps({"error": "No Letterboxd username provided."}))
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(1.5)

# ── Parse watchlist ───────────────────────────────────────────────────────────
class WatchlistParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.movies = []   # list of {"slug": ..., "name": ...}
        self.last_page = 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        # Pattern 1: data-film-slug attribute
        if tag == "div" and attrs.get("data-film-slug"):
            slug = attrs["data-film-slug"]
            name = attrs.get("data-film-name", slug.replace("-", " ").title())
            if not any(m["slug"] == slug for m in self.movies):
                self.movies.append({"slug": slug, "name": name})
        # Pattern 2: data-target-link="/film/{slug}/"
        if tag in ("div", "li", "a", "article") and attrs.get("data-target-link", "").startswith("/film/"):
            m = re.match(r"^/film/([^/]+)/$", attrs["data-target-link"])
            if m:
                slug = m.group(1)
                name = attrs.get("data-film-name", slug.replace("-", " ").title())
                if not any(mv["slug"] == slug for mv in self.movies):
                    self.movies.append({"slug": slug, "name": name})
        # Detect last page number from pagination
        if tag == "a" and attrs.get("href", ""):
            m = re.search(r"/watchlist/page/(\d+)/$", attrs["href"])
            if m:
                p = int(m.group(1))
                if p > self.last_page:
                    self.last_page = p

def scrape_watchlist(username):
    base = f"https://letterboxd.com/{username}/watchlist"
    # First page to discover total pages
    html = fetch(f"{base}/")
    parser = WatchlistParser()
    parser.feed(html)
    total_pages = parser.last_page

    if total_pages > 1:
        for page in range(2, total_pages + 1):
            try:
                html = fetch(f"{base}/page/{page}/")
                parser.feed(html)
                time.sleep(0.4)
            except Exception:
                pass

    return parser.movies

# ── Parse film detail page ────────────────────────────────────────────────────
class FilmParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.json_ld = None
        self._in_script = False
        self._script_type = ""
        self._buf = ""
        self.genres = []
        self._in_genre_section = False
        self.poster_url = ""

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "script":
            if "application/ld+json" in attrs_d.get("type", ""):
                self._in_script = True
                self._script_type = "json_ld"
                self._buf = ""
        if tag == "a" and "genre" in attrs_d.get("href", ""):
            self._in_genre_section = True
        if tag == "img" and attrs_d.get("src", "").startswith("https://a.ltrbxd.com"):
            if not self.poster_url:
                self.poster_url = attrs_d["src"]

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            self._in_script = False
            if self._script_type == "json_ld":
                try:
                    raw = self._buf.strip()
                    # Strip CDATA wrappers: /* <![CDATA[ */ ... /* ]]> */
                    raw = re.sub(r'/\*\s*<!\[CDATA\[\s*\*/', '', raw)
                    raw = re.sub(r'/\*\s*\]\]>\s*\*/', '', raw)
                    self.json_ld = json.loads(raw.strip())
                except Exception:
                    pass
            self._buf = ""
        if tag == "a" and self._in_genre_section:
            self._in_genre_section = False

    def handle_data(self, data):
        if self._in_script:
            self._buf += data
        if self._in_genre_section:
            t = data.strip()
            if t and t not in self.genres:
                self.genres.append(t)

def get_film_details(slug):
    url = f"https://letterboxd.com/film/{slug}/"
    html = fetch(url)
    parser = FilmParser()
    parser.feed(html)

    details = {
        "slug": slug, "url": url, "genres": [], "runtime": None,
        "rating": None, "year": None, "synopsis": "", "poster": "",
        "director": "", "title": slug.replace("-", " ").title()
    }

    # Extract from JSON-LD structured data
    if parser.json_ld:
        jl = parser.json_ld
        details["title"] = jl.get("name", details["title"])
        details["synopsis"] = jl.get("description", "")
        details["rating"] = jl.get("aggregateRating", {}).get("ratingValue")
        # Year: try datePublished first, then releasedEvent
        if jl.get("datePublished"):
            details["year"] = str(jl["datePublished"])[:4]
        elif jl.get("releasedEvent"):
            ev = jl["releasedEvent"]
            if isinstance(ev, list) and ev:
                details["year"] = str(ev[0].get("startDate", ""))[:4]
            elif isinstance(ev, dict):
                details["year"] = str(ev.get("startDate", ""))[:4]
        details["poster"] = jl.get("image", "")
        directors = jl.get("director", [])
        if isinstance(directors, list) and directors:
            details["director"] = ", ".join(d.get("name", "") for d in directors)
        elif isinstance(directors, dict):
            details["director"] = directors.get("name", "")
        genres_raw = jl.get("genre", [])
        if isinstance(genres_raw, list):
            details["genres"] = [g.lower() for g in genres_raw]
        elif isinstance(genres_raw, str):
            details["genres"] = [genres_raw.lower()]

    # Runtime — Letterboxd uses "97&nbsp;mins" format
    rt_match = re.search(r'(\d+)(?:&nbsp;|\s*)mins?', html, re.IGNORECASE)
    if rt_match:
        details["runtime"] = int(rt_match.group(1))

    # Synopsis fallback: og:description or meta description
    og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if not details["synopsis"] and og_desc:
        details["synopsis"] = og_desc.group(1)
    if not details["synopsis"]:
        meta_desc = re.search(r'<meta name="description" content="([^"]+)"', html)
        if meta_desc:
            details["synopsis"] = meta_desc.group(1)

    # Poster: prefer og:image (higher resolution)
    og_img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if og_img:
        details["poster"] = og_img.group(1)

    # Genre fallback from sidebar links
    genre_links = re.findall(r'href="/films/genre/([^/"]+)/"', html)
    if genre_links and not details["genres"]:
        details["genres"] = list(set(g.replace("-", " ") for g in genre_links))

    return details

# ── Filter logic ──────────────────────────────────────────────────────────────
def passes_filters(details):
    # Genre filter
    if GENRE_FILTER and GENRE_FILTER != "any":
        movie_genres = [g.lower() for g in details.get("genres", [])]
        if not any(GENRE_FILTER in g for g in movie_genres):
            return False
    # Rating filter
    rating = details.get("rating")
    if rating is not None:
        try:
            if float(rating) < MIN_RATING:
                return False
        except Exception:
            pass
    elif MIN_RATING > 0:
        return False  # no rating data — skip if filter is active
    # Runtime filter
    runtime = details.get("runtime")
    if RUNTIME_FILTER == "short":
        if runtime is None or runtime >= 60:
            return False
    elif RUNTIME_FILTER == "feature":
        if runtime is None or runtime < 60:
            return False
    return True

# ── Main ──────────────────────────────────────────────────────────────────────
print(f"🎬 Fetching watchlist for @{USERNAME}...", flush=True)

try:
    movies = scrape_watchlist(USERNAME)
except Exception as e:
    print(json.dumps({"error": f"Could not fetch watchlist. Is the profile public? ({e})"}))
    sys.exit(1)

if not movies:
    print(json.dumps({"error": f"No movies found in @{USERNAME}'s watchlist. Is the profile public?"}))
    sys.exit(1)

print(f"✅ Found {len(movies)} movies in watchlist.", flush=True)

# Shuffle and iterate, applying filters
random.shuffle(movies)
MAX_ATTEMPTS = min(30, len(movies))
picked = None

print("🔍 Applying filters and picking a movie...", flush=True)

for candidate in movies[:MAX_ATTEMPTS]:
    details = get_film_details(candidate["slug"])
    if passes_filters(details):
        picked = details
        break
    time.sleep(0.3)

if not picked:
    # Fallback: ignore filters and pick any random movie
    print("⚠️  No movies matched your filters; picking any random movie.", flush=True)
    candidate = random.choice(movies)
    picked = get_film_details(candidate["slug"])

# ── Output ────────────────────────────────────────────────────────────────────
result = {
    "title":           picked.get("title", "Unknown"),
    "year":            picked.get("year", ""),
    "director":        picked.get("director", ""),
    "synopsis":        picked.get("synopsis", "No synopsis available."),
    "rating":          picked.get("rating"),
    "runtime":         picked.get("runtime"),
    "genres":          picked.get("genres", []),
    "poster":          picked.get("poster", ""),
    "url":             picked.get("url", ""),
    "watchlist_total": len(movies),
    "filters_applied": {
        "genre":      GENRE_FILTER or "any",
        "min_rating": MIN_RATING,
        "runtime":    RUNTIME_FILTER,
    }
}

print("\n[LETTERBOXD_RESULT:" + json.dumps(result) + "]")
