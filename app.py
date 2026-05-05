#!/usr/bin/env python3
"""
Letterboxd Watchlist Random Movie Picker - Web App Version

How to run:
1. Save this file as app.py
2. Install Flask:
   py -m pip install flask
3. Optional for streaming-provider filtering:
   Set a TMDB API key as an environment variable named TMDB_API_KEY
4. Run:
   py app.py
5. Open your browser to:
   http://127.0.0.1:5000
"""

import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Streaming provider names as TMDb commonly returns them.
# The code checks names instead of hard-coding provider IDs, which is easier to maintain.
STREAMING_PROVIDER_NAMES = {
    "any": [],
    "netflix": ["Netflix"],
    "max": ["Max", "HBO Max"],
    "hulu": ["Hulu"],
    "paramount": ["Paramount Plus", "Paramount+", "Paramount+ with Showtime"],
    "tubi": ["Tubi TV", "Tubi"],
    "pluto": ["Pluto TV"],
}

# Simple in-memory caches make re-rolls much faster while the app is running.
WATCHLIST_CACHE = {}
FILM_DETAILS_CACHE = {}
STREAMING_CACHE = {}


def fetch(url, retries=3, headers=None):
    headers = headers or HEADERS
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
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
    if username in WATCHLIST_CACHE:
        return WATCHLIST_CACHE[username]

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

    WATCHLIST_CACHE[username] = parser.movies
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
    if slug in FILM_DETAILS_CACHE:
        return dict(FILM_DETAILS_CACHE[slug])

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
        "streaming_providers": [],
        "tmdb_match_found": False,
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

    FILM_DETAILS_CACHE[slug] = dict(details)
    return details


def tmdb_request(url):
    """Request TMDb using either a v3 API key or a v4 bearer token."""
    tmdb_api_key = os.environ.get("TMDB_API_KEY", "").strip()
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN", "").strip()

    if tmdb_bearer_token:
        headers = dict(HEADERS)
        headers["Authorization"] = f"Bearer {tmdb_bearer_token}"
        return json.loads(fetch(url, headers=headers))

    if tmdb_api_key:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api_key={urllib.parse.quote(tmdb_api_key)}"
        return json.loads(fetch(url))

    raise ValueError(
        "Streaming-platform filtering needs a TMDb API key. "
        "For now, choose Streaming Platform = Any, or add TMDB_API_KEY on your computer."
    )


def get_tmdb_movie_id(title, year=""):
    params = {
        "query": title,
        "include_adult": "false",
        "language": "en-US",
        "page": "1",
    }
    if year:
        params["year"] = str(year)

    url = "https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(params)
    data = tmdb_request(url)
    results = data.get("results", [])

    if not results and year:
        # Retry without year if the year match fails.
        params.pop("year", None)
        url = "https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(params)
        data = tmdb_request(url)
        results = data.get("results", [])

    if not results:
        return None

    return results[0].get("id")


def get_streaming_providers(title, year="", country="US"):
    cache_key = (title.lower().strip(), str(year), country.upper())
    if cache_key in STREAMING_CACHE:
        return STREAMING_CACHE[cache_key]

    movie_id = get_tmdb_movie_id(title, year)
    if not movie_id:
        STREAMING_CACHE[cache_key] = []
        return []

    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers"
    data = tmdb_request(url)
    country_data = data.get("results", {}).get(country.upper(), {})

    provider_sections = ["flatrate", "free", "ads"]
    providers = []

    for section in provider_sections:
        for provider in country_data.get(section, []):
            name = provider.get("provider_name")
            if name and name not in providers:
                providers.append(name)

    STREAMING_CACHE[cache_key] = providers
    return providers


def streaming_provider_matches(details, streaming_filter):
    if not streaming_filter or streaming_filter == "any":
        return True

    wanted_names = STREAMING_PROVIDER_NAMES.get(streaming_filter, [])
    if not wanted_names:
        return True

    providers = get_streaming_providers(details.get("title", ""), details.get("year", ""))
    details["streaming_providers"] = providers
    details["tmdb_match_found"] = bool(providers)

    provider_names_lower = [p.lower() for p in providers]
    wanted_names_lower = [w.lower() for w in wanted_names]

    return any(wanted in provider for wanted in wanted_names_lower for provider in provider_names_lower)


def passes_filters(details, genre_filter, min_rating, runtime_filter, streaming_filter):
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
    if runtime_filter == "under120":
        if runtime is None or runtime >= 120:
            return False
    elif runtime_filter == "feature":
        if runtime is None or runtime < 60:
            return False

    if not streaming_provider_matches(details, streaming_filter):
        return False

    return True


def pick_movie(username, genre_filter="any", min_rating=0, runtime_filter="any", streaming_filter="any"):
    username = username.strip().lower().replace("@", "")
    movies = scrape_watchlist(username)

    if not movies:
        raise ValueError(f"No movies found in @{username}'s watchlist. Make sure the profile/watchlist is public.")

    random.shuffle(movies)
    # Higher attempts = better matching, but slower.
    # 20 is a good balance for local testing.
    max_attempts = min(20, len(movies))
    picked = None
    filters_used = True

    for candidate in movies[:max_attempts]:
        details = get_film_details(candidate["slug"])
        if passes_filters(details, genre_filter, min_rating, runtime_filter, streaming_filter):
            picked = details
            break

    if not picked:
        filters_used = False
        candidate = random.choice(movies)
        picked = get_film_details(candidate["slug"])
        if streaming_filter and streaming_filter != "any":
            try:
                picked["streaming_providers"] = get_streaming_providers(picked.get("title", ""), picked.get("year", ""))
            except Exception:
                picked["streaming_providers"] = []

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
      max-width: 1100px;
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
      grid-template-columns: repeat(6, 1fr);
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
    button:disabled {
      opacity: 0.7;
      cursor: not-allowed;
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
    .streaming-pill {
      background: #15351d;
      border-color: #00c030;
      color: #d8ffe0;
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
      margin-bottom: 16px;
    }
    .note {
      background: #32270f;
      border: 1px solid #80641e;
      padding: 12px;
      border-radius: 12px;
      color: #ffe7a6;
      margin-bottom: 14px;
    }
    .wide {
      grid-column: span 2;
    }
    @media (max-width: 1000px) {
      form {
        grid-template-columns: repeat(2, 1fr);
      }
      .wide {
        grid-column: span 1;
      }
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
          <option value="under120" {% if runtime == 'under120' %}selected{% endif %}>Under 120 min</option>
          <option value="feature" {% if runtime == 'feature' %}selected{% endif %}>Feature 60+ min</option>
        </select>
      </div>
      <div class="wide">
        <label for="streaming">Streaming Platform</label>
        <select id="streaming" name="streaming">
          <option value="any" {% if streaming == 'any' %}selected{% endif %}>Any / No streaming filter</option>
          <option value="netflix" {% if streaming == 'netflix' %}selected{% endif %}>Netflix</option>
          <option value="max" {% if streaming == 'max' %}selected{% endif %}>Max / HBO Max</option>
          <option value="hulu" {% if streaming == 'hulu' %}selected{% endif %}>Hulu</option>
          <option value="paramount" {% if streaming == 'paramount' %}selected{% endif %}>Paramount+</option>
          <option value="tubi" {% if streaming == 'tubi' %}selected{% endif %}>Tubi</option>
          <option value="pluto" {% if streaming == 'pluto' %}selected{% endif %}>Pluto TV</option>
        </select>
      </div>
      <button type="submit" id="pickButton">Pick Movie</button>
    </form>

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}

    <div id="resultArea">
    {% if movie %}
      {% if not movie.filters_used %}
        <div class="note">No movie matched every filter, so this pick ignores one or more filters.</div>
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

          {% if movie.streaming_providers %}
            <p><strong>Available on:</strong></p>
            {% for provider in movie.streaming_providers %}
              <span class="pill streaming-pill">{{ provider }}</span>
            {% endfor %}
          {% elif streaming != 'any' %}
            <p><strong>Streaming:</strong> No provider match found through TMDb for this pick.</p>
          {% endif %}

          <p>{{ movie.synopsis }}</p>
          <p>Picked from {{ movie.watchlist_total }} total watchlist movies.</p>
          <a class="link" href="{{ movie.url }}" target="_blank">View on Letterboxd →</a>
        </div>
      </section>
    {% endif %}
    </div>
  </main>

  <script>
    const form = document.querySelector("form");
    const button = document.querySelector("#pickButton");
    const resultArea = document.querySelector("#resultArea");

    function escapeHtml(value) {
      if (value === null || value === undefined) return "";
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function movieCardHtml(movie) {
      const genres = (movie.genres || [])
        .map(genre => `<span class="pill">${escapeHtml(genre)}</span>`)
        .join("");

      const streaming = (movie.streaming_providers || [])
        .map(provider => `<span class="pill streaming-pill">${escapeHtml(provider)}</span>`)
        .join("");

      const streamingBlock = streaming
        ? `<p><strong>Available on:</strong></p>${streaming}`
        : "";

      const note = movie.filters_used
        ? ""
        : `<div class="note">No movie matched every filter, so this pick ignores one or more filters.</div>`;

      return `
        ${note}
        <section class="card">
          ${movie.poster
            ? `<img class="poster" src="${escapeHtml(movie.poster)}" alt="Poster for ${escapeHtml(movie.title)}">`
            : `<div class="poster"></div>`
          }
          <div class="content">
            <h2 class="title">${escapeHtml(movie.title)} ${movie.year ? `(${escapeHtml(movie.year)})` : ""}</h2>
            <div class="meta">
              ${movie.director ? `Directed by ${escapeHtml(movie.director)} · ` : ""}
              ${movie.runtime ? `${escapeHtml(movie.runtime)} mins · ` : ""}
              ${movie.rating ? `⭐ ${escapeHtml(movie.rating)}/5` : ""}
            </div>

            ${genres}
            ${streamingBlock}

            <p>${escapeHtml(movie.synopsis)}</p>
            <p>Picked from ${escapeHtml(movie.watchlist_total)} total watchlist movies.</p>
            <a class="link" href="${escapeHtml(movie.url)}" target="_blank">View on Letterboxd →</a>
          </div>
        </section>
      `;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.textContent = "Picking...";
      button.disabled = true;
      resultArea.innerHTML = `<div class="note">Picking a movie from your watchlist...</div>`;

      try {
        const response = await fetch(`${window.location.origin}/api/pick`, {
          method: "POST",
          body: new FormData(form),
        });

        const data = await response.json();

        if (!response.ok || data.error) {
          resultArea.innerHTML = `<div class="error">${escapeHtml(data.error || "Something went wrong.")}</div>`;
        } else {
          resultArea.innerHTML = movieCardHtml(data.movie);
          button.textContent = "Re-roll";
        }
      } catch (error) {
        resultArea.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      }

      button.disabled = false;
      if (button.textContent !== "Re-roll") {
        button.textContent = "Pick Movie";
      }
    });
  </script>
</body>
</html>
"""


@app.route("/api/pick", methods=["POST"])
def api_pick():
    username = request.form.get("username", "").strip()
    genre = request.form.get("genre", "any").strip().lower() or "any"
    min_rating = request.form.get("min_rating", "0").strip() or "0"
    runtime = request.form.get("runtime", "any").strip().lower() or "any"
    streaming = request.form.get("streaming", "any").strip().lower() or "any"

    try:
        movie = pick_movie(
            username=username,
            genre_filter=genre,
            min_rating=float(min_rating),
            runtime_filter=runtime,
            streaming_filter=streaming,
        )
        return jsonify({"movie": movie})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/", methods=["GET", "POST"])
def index():
    movie = None
    error = None
    username = "MaxLubelczyk"
    genre = "any"
    min_rating = "0"
    runtime = "any"
    streaming = "any"

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        genre = request.form.get("genre", "any").strip().lower() or "any"
        min_rating = request.form.get("min_rating", "0").strip() or "0"
        runtime = request.form.get("runtime", "any").strip().lower() or "any"
        streaming = request.form.get("streaming", "any").strip().lower() or "any"

        try:
            movie = pick_movie(
                username=username,
                genre_filter=genre,
                min_rating=float(min_rating),
                runtime_filter=runtime,
                streaming_filter=streaming,
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
        streaming=streaming,
    )


if __name__ == "__main__":
    app.run(debug=True)
