from collections import deque
from urllib.parse import urlparse

def crawl_and_archive(
    seeds,
    max_pages=20,
    same_domain_only=True,
    allowed_keywords=None,
):
    """
    seeds: list of starting URLs
    max_pages: hard cap
    same_domain_only: if True, don't leave the first seed's domain
    allowed_keywords: list of lowercase substrings; if set, we only enqueue
                      links whose URL contains any of them
    """
    ensure_archive_table(DB_PATH)

    visited = set()
    queue = deque()

    # derive primary domain from the first seed
    if same_domain_only and seeds:
        root_domain = urlparse(seeds[0]).netloc
    else:
        root_domain = None

    for s in seeds:
        queue.append((s, 0))  # (url, depth)

    pages_archived = 0

    while queue and pages_archived < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"[skip] {url} ({e})")
            continue

        title = extract_title(html)
        print(f"[archive] {url}  ->  {title!r}")
        save_archive_page(DB_PATH, url, title, html)
        pages_archived += 1

        # link discovery
        links = extract_links(html, url)
        for link in links:
            # normalize / filter
            if same_domain_only and root_domain:
                if urlparse(link).netloc != root_domain:
                    continue

            if allowed_keywords:
                lowered = link.lower()
                if not any(kw in lowered for kw in allowed_keywords):
                    continue

            if link not in visited:
                queue.append((link, depth + 1))

        # optional politeness delay (don't hammer)
        time.sleep(0.5)

