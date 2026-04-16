"""
Web search service — DuckDuckGo scraping and weather widget.
"""

import re
import urllib.request
import urllib.parse


def perform_web_search(query: str) -> str:
    """
    Scrape DuckDuckGo for snippets and optionally fetch weather data.
    Returns a context string to inject into the LLM prompt.
    """
    web_context = ""

    # Hack for weather queries since DDG text limits widget scraping
    if "weather" in query.lower():
        loc = "".join(query.lower().split("weather")[1:]).replace("in", "").replace("like", "").replace("for", "").replace("?", "").replace("right now", "").strip()
        if loc:
            try:
                req = urllib.request.Request(
                    f"https://wttr.in/{urllib.parse.quote(loc)}?format=3",
                    headers={'User-Agent': 'curl'}
                )
                w_res = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
                web_context += f"### Live Weather Widget Data ###\nLocation/Weather: {w_res.strip()}\n\n"
            except Exception as e:
                print("wttr.in fail:", e)

    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36"
        })
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8")

        snippets = re.findall(r"<a class=\"result__snippet[^>]*>(.*?)</a>", html, re.DOTALL | re.IGNORECASE)

        if snippets:
            web_context += "### Live Web Search Context ###\n"
            for s in snippets[:3]:
                clean_text = re.sub(r"<[^>]+>", "", s).strip()
                clean_text = clean_text.replace("&#x27;", "'").replace("&quot;", '"')
                web_context += f"Snippet: {clean_text}\n\n"
    except Exception as e:
        print(f"Web search failed: {e}")

    if not web_context:
        web_context = "System Note: Live Web search is currently temporarily blocked or failing. Tell the user you couldn't access the live web context right now."

    return web_context
