#!/usr/bin/env python3
"""
Letterboxd Watchlist Random Movie Picker - Web App Version

How to run:
1. Save this file as app.py
2. Install Flask:
   pip install flask
3. Run:
   python app.py
4. Open your browser to:
   http://127.0.0.1:5000
"""

import json
import random
import re
import time
import urllib.request
from html.parser import HTMLParser
from flask import Flask, request, render_template_string

app = Flask(__name__)

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
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5)


class WatchlistParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.movies = []
        self.last_page = 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "div" and attrs.get("data-film-slug"):
            slug = attrs["data-film-slug"]
            name = attrs.get("data-film-name", slug.replace("-", " ").title())
            if not any(m["slug"] == slug for m in self.movies):
                self.movies.append({"slug": slug, "name": name})

        if tag in ("div", "li", "a", "article") and attrs.get("data-target-link", "").startswith("/film/"):
            match = re.match(r"^/film/([^/]+)/$", attrs["data-target-link"])
            if match:
                slug = match.group(1)
                name = attrs.get("data-film-name", slug.replace("-", " ").title())
                if not any(mv["slug"] == slug for mv in self.movies):
                    self.movies.append({"slug": slug, "name": name})

        if tag == "a" and attrs.get("href", ""):
            match = re.search(r"/watchlist/page/(\d+)/$", attrs["href"])
            if match:
                page = int(match.group(1))
                if page > self.last_page:
                    self.last_page = page


def scrape_watchlist(username):
    base = f"https://letterboxd.com/{username}/watchlist"
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


class FilmParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.json_ld = None
        self._in_script = False
        self._script_type = ""
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "script" and "application/ld+json" in attrs_d.get("type", ""):
            self._in_script = True
            self._script_type = "json_ld"
            self._buf = ""

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            self._in_script = False
            if self._script_type == "json_ld":
                try:
                    raw = self._buf.strip()
                    raw = re.sub(r'/\*\s*<!\[CDATA\[\s*\*/', '', raw)
                    raw = re.sub(r'/\*\s*\]\]>\s*\*/', '', raw)
                    self.json_ld = json.loads(raw.strip())
                except Exception:
                    pass
            self._buf = ""

    def handle_data(self, data):
        if self._in_script:
            self._buf += data


def get_film_details(slug):
    url = f"https://letterboxd.com/film/{slug}/"
    html = fetch(url)
    parser = FilmParser()
    parser.feed(html)

    details = {
        "slug": slug,
        "url": url,
        "genres": [],
        "runtime": None,
        "rating": None,
        "year": "",
        "synopsis": "No synopsis available.",
        "poster": "",
        "director": "",
        "title": slug.replace("-", " ").title(),
    }

    if parser.json_ld:
        jl = parser.json_ld
        details["title"] = jl.get("name", details["title"])
        details["synopsis"] = jl.get("description", details["synopsis"])
        details["rating"] = jl.get("aggregateRating", {}).get("ratingValue")

        if jl.get("datePublished"):
            details["year"] = str(jl["datePublished"])[:4]
        elif jl.get("releasedEvent"):
            event = jl["releasedEvent"]
            if isinstance(event, list) and event:
                details["year"] = str(event[0].get("startDate", ""))[:4]
            elif isinstance(event, dict):
                details["year"] = str(event.get("startDate", ""))[:4]

        details["poster"] = jl.get("image", "")

        directors = jl.get("director", [])
        if isinstance(directors, list) and directors:
            details["director"] = ", ".join(d.get("name", "") for d in directors if d.get("name"))
        elif isinstance(directors, dict):
            details["director"] = directors.get("name", "")

        genres_raw = jl.get("genre", [])
        if isinstance(genres_raw, list):
            details["genres"] = [g.lower() for g in genres_raw]
        elif isinstance(genres_raw, str):
            details["genres"] = [genres_raw.lower()]

    runtime_match = re.search(r'(\d+)(?:&nbsp;|\s*)mins?', html, re.IGNORECASE)
    if runtime_match:
        details["runtime"] = int(runtime_match.group(1))

    og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if details["synopsis"] == "No synopsis available." and og_desc:
        details["synopsis"] = og_desc.group(1)

    og_img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if og_img:
        details["poster"] = og_img.group(1)

    return details


def passes_filters(details, genre_filter, min_rating, runtime_filter):
    if genre_filter and genre_filter != "any":
        movie_genres = [g.lower() for g in details.get("genres", [])]
        if not any(genre_filter in g for g in movie_genres):
            return False

    rating = details.get("rating")
    if rating is not None:
        try:
            if float(rating) < min_rating:
                return False
        except Exception:
            pass
    elif min_rating > 0:
        return False

    runtime = details.get("runtime")
    if runtime_filter == "short":
        if runtime is None or runtime >= 120:
            return False
    elif runtime_filter == "feature":
        if runtime is None or runtime < 60:
            return False

    return True


def pick_movie(username, genre_filter="any", min_rating=0, runtime_filter="any"):
    username = username.strip().lower().replace("@", "")
    movies = scrape_watchlist(username)

    if not movies:
        raise ValueError(f"No movies found in @{username}'s watchlist. Make sure the profile/watchlist is public.")

    random.shuffle(movies)
    max_attempts = min(30, len(movies))
    picked = None
    filters_used = True

    for candidate in movies[:max_attempts]:
        details = get_film_details(candidate["slug"])
        if passes_filters(details, genre_filter, min_rating, runtime_filter):
            picked = details
            break
        time.sleep(0.3)

    if not picked:
        filters_used = False
        candidate = random.choice(movies)
        picked = get_film_details(candidate["slug"])

    picked["watchlist_total"] = len(movies)
    picked["filters_used"] = filters_used
    return picked


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Random Letterboxd Picker</title>
  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #111;
      color: #f4f4f4;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 30px;
    }
    .container {
      width: 100%;
      max-width: 950px;
    }
    .hero {
      text-align: center;
      margin-bottom: 28px;
    }
    h1 {
      font-size: 42px;
      margin-bottom: 8px;
    }
    p {
      color: #cfcfcf;
      line-height: 1.5;
    }
    form {
      background: #1c1c1c;
      border: 1px solid #333;
      border-radius: 18px;
      padding: 22px;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-bottom: 24px;
    }
    label {
      display: block;
      font-size: 13px;
      color: #aaa;
      margin-bottom: 6px;
    }
    input, select, button {
      width: 100%;
      box-sizing: border-box;
      border-radius: 10px;
      border: 1px solid #444;
      padding: 12px;
      background: #101010;
      color: #fff;
      font-size: 15px;
    }
    button {
      background: #00c030;
      color: #071107;
      border: none;
      cursor: pointer;
      font-weight: bold;
      align-self: end;
    }
    button:hover {
      filter: brightness(1.1);
    }
    .card {
      background: #1c1c1c;
      border: 1px solid #333;
      border-radius: 22px;
      overflow: hidden;
      display: grid;
      grid-template-columns: 260px 1fr;
      gap: 0;
    }
    .poster {
      width: 100%;
      height: 100%;
      object-fit: cover;
      background: #333;
    }
    .content {
      padding: 26px;
    }
    .title {
      font-size: 34px;
      margin: 0 0 8px;
    }
    .meta {
      color: #aaa;
      margin-bottom: 18px;
    }
    .pill {
      display: inline-block;
      background: #292929;
      border: 1px solid #444;
      padding: 7px 10px;
      border-radius: 999px;
      margin: 0 6px 6px 0;
      font-size: 13px;
      color: #ddd;
    }
    .link {
      display: inline-block;
      margin-top: 16px;
      color: #00e054;
      text-decoration: none;
      font-weight: bold;
    }
    .error {
      background: #3b1010;
      border: 1px solid #7d2b2b;
      color: #ffd7d7;
      padding: 18px;
      border-radius: 14px;
    }
    .note {
      background: #32270f;
      border: 1px solid #80641e;
      padding: 12px;
      border-radius: 12px;
      color: #ffe7a6;
      margin-bottom: 14px;
    }
    @media (max-width: 800px) {
      form, .card {
        grid-template-columns: 1fr;
      }
      .poster {
        max-height: 420px;
      }
    }
  </style>
</head>
<body>
  <main class="container">
    <section class="hero">
      <h1>🎬 Random Letterboxd Picker</h1>
      <p>Enter a public Letterboxd username and let the app pick something from the watchlist.</p>
    </section>

    <form method="POST">
      <div>
        <label for="username">Letterboxd username</label>
        <input id="username" name="username" value="{{ username }}" placeholder="MaxLubelczyk" required>
      </div>
      <div>
        <label for="genre">Genre</label>
        <input id="genre" name="genre" value="{{ genre }}" placeholder="any, horror, comedy">
      </div>
      <div>
        <label for="min_rating">Minimum rating</label>
        <input id="min_rating" name="min_rating" value="{{ min_rating }}" placeholder="0" type="number" min="0" max="5" step="0.1">
      </div>
      <div>
        <label for="runtime">Runtime</label>
        <select id="runtime" name="runtime">
          <option value="any" {% if runtime == 'any' %}selected{% endif %}>Any</option>
          <option value="short" {% if runtime == 'short' %}selected{% endif %}>Under 120 min</option>
          <option value="feature" {% if runtime == 'feature' %}selected{% endif %}>Feature 60+ min</option>
        </select>
      </div>
      <button type="submit">Pick Movie</button>
    </form>

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}

    {% if movie %}
      {% if not movie.filters_used %}
        <div class="note">No movie matched your filters, so this pick ignores the filters.</div>
      {% endif %}

      <section class="card">
        {% if movie.poster %}
          <img class="poster" src="{{ movie.poster }}" alt="Poster for {{ movie.title }}">
        {% else %}
          <div class="poster"></div>
        {% endif %}
        <div class="content">
          <h2 class="title">{{ movie.title }} {% if movie.year %}({{ movie.year }}){% endif %}</h2>
          <div class="meta">
            {% if movie.director %}Directed by {{ movie.director }} · {% endif %}
            {% if movie.runtime %}{{ movie.runtime }} mins · {% endif %}
            {% if movie.rating %}⭐ {{ movie.rating }}/5{% endif %}
          </div>

          {% for genre in movie.genres %}
            <span class="pill">{{ genre }}</span>
          {% endfor %}

          <p>{{ movie.synopsis }}</p>
          <p>Picked from {{ movie.watchlist_total }} total watchlist movies.</p>
          <a class="link" href="{{ movie.url }}" target="_blank">View on Letterboxd →</a>
        </div>
      </section>
    {% endif %}
  </main>

<script>
  const form = document.querySelector("form");
  const button = document.querySelector("button");

  form.addEventListener("submit", () => {
    button.textContent = "Picking...";
    button.disabled = true;
  });
</script>
  
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    movie = None
    error = None
    username = "MaxLubelczyk"
    genre = "any"
    min_rating = "0"
    runtime = "any"

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        genre = request.form.get("genre", "any").strip().lower() or "any"
        min_rating = request.form.get("min_rating", "0").strip() or "0"
        runtime = request.form.get("runtime", "any").strip().lower() or "any"

        try:
            movie = pick_movie(
                username=username,
                genre_filter=genre,
                min_rating=float(min_rating),
                runtime_filter=runtime,
            )
        except Exception as exc:
            error = str(exc)

    return render_template_string(
        HTML,
        movie=movie,
        error=error,
        username=username,
        genre=genre,
        min_rating=min_rating,
        runtime=runtime,
    )


if __name__ == "__main__":
    app.run(debug=True)

