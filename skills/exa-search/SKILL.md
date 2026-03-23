---
name: exa-search
description: "Search the web, fetch page content, and find code examples using the Exa MCP server. Use this skill whenever the user asks to search the web, look up news, find current information, research a topic online, fetch/crawl a URL, or find code examples and documentation. Also trigger when the user mentions 'exa' by name, asks 'what's happening with X', wants recent articles or papers, or needs to pull content from a webpage. This skill covers all three Exa tools: web_search_exa, crawling_exa, and get_code_context_exa."
---

# Exa Search -- Web Search, Crawling & Code Context via MCP

Use the Exa MCP server to search the web, crawl pages for full content, and find code examples. Exa exposes three tools: `web_search_exa`, `crawling_exa`, and `get_code_context_exa`.

## Critical Caveats

1. **On `web_search_exa`, use either `maxAgeHours` or `livecrawl` -- never both.** The server treats them as mutually exclusive and returns a 400 error like: *"Cannot set both 'livecrawl' and 'maxAgeHours'"*.
   - Use `maxAgeHours` when you need a **hard recency window** such as "last 24 hours".
   - Use `livecrawl: "preferred"` when you want the **freshest possible search fetch** but do **not** need a strict age bound.
   - Treat `freshness` as compatibility sugar for `maxAgeHours`, not as a separate mode.

2. **Prefer `maxAgeHours` over `freshness` when you can.** In some environments, generated wrappers lag behind the server schema: they may expose `freshness` but not `maxAgeHours`, and `freshness` can still collide with an implicit/default `livecrawl` value. If you hit the 400 even though you did not explicitly set `livecrawl`, call the MCP tool directly and pass `maxAgeHours` yourself.

3. **Queries should be semantic descriptions, not keywords.** Exa uses neural search -- describe the ideal page you want to find.
   - Good: `"blog post comparing React and Vue performance benchmarks in 2026"`
   - Bad: `"React vs Vue performance"`

4. **Use `category` to sharpen results.** When searching for news, set `category: "news"`. For academic work, use `"research paper"`. This significantly improves relevance over relying on query text alone.

5. **`type: "fast"` is useful for straightforward lookups** where you don't need deep semantic matching. Use `"auto"` (or omit) for complex or nuanced queries.

6. **On `crawling_exa`, `maxAgeHours` controls page-cache freshness.**
   - `maxAgeHours: 0` = always livecrawl the page now.
   - Omit `maxAgeHours` = use cached content with livecrawl fallback.
   - Use a positive value like `24` when cached content up to that age is acceptable.

7. **Batch URLs in `crawling_exa`.** Don't make separate calls per URL -- pass them all in one `urls` array.

8. **Increase `maxCharacters` for long-form content in `crawling_exa`.** The 3000-character default truncates most articles. Use 10000-30000 for full articles.

## Query Patterns

| User intent | Recommended approach |
|-------------|---------------------|
| Today's news | `category: "news"`, `maxAgeHours: 24`, describe the topic in `query`, omit `livecrawl` |
| Recent news (this week) | `category: "news"`, `maxAgeHours: 168` |
| Need freshest possible search results without a hard time window | `livecrawl: "preferred"`, omit `maxAgeHours`/`freshness` |
| Find a company | `category: "company"`, describe the company |
| Academic papers | `category: "research paper"`, describe the research topic |
| Tweets about X | `category: "tweet"`, describe the topic |
| Results from specific sites | Use `includeDomains`, e.g. `["reuters.com", "apnews.com"]` |
| Crawl a known article/page and force a fresh fetch | `crawling_exa` with `urls: [...]`, `maxAgeHours: 0` |
| General web search | Just `query` and `numResults` -- omit category |

## Workflow: Search then Crawl

A common two-step pattern when the user needs in-depth content:

1. **Search** with `web_search_exa` to find relevant URLs and get highlights.
2. **Crawl** the most relevant URLs with `crawling_exa` if the highlights are insufficient.

This avoids wasting crawl quota on irrelevant pages.

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| 400 error about `livecrawl` and `maxAgeHours` | Never combine `livecrawl` with `maxAgeHours`. If you only set `freshness` and still see this, bypass the wrapper and call `web_search_exa` directly with `maxAgeHours` |
| Not sure whether to use `livecrawl` or `maxAgeHours` | Use `maxAgeHours` for a strict recency filter; use `livecrawl` when you want a fresh fetch but no hard age limit |
| Results are too generic or off-topic | Add a `category` filter and make the query more descriptive |
| Highlights are truncated | Follow up with `crawling_exa` on the best URLs, with a higher `maxCharacters` |
| Too few results | Increase `numResults`, broaden the query, or remove `includeDomains` |
| Stale results for news | Prefer `maxAgeHours` such as `24` or `168`; if you want a live fetch without a time window, use `livecrawl: "preferred"` |
| Code search returns too little context | Increase `tokensNum` on `get_code_context_exa` |

## Output Format

All three tools return results as JSON. Each result typically includes `title`, `url`, `publishedDate`, `author`, and `highlights` (or full `text` for crawling). Parse and present these fields to the user in a readable format -- tables work well for lists of results.
