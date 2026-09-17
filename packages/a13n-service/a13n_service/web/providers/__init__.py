"""Built-in Web Provider dispatch, with one module per upstream API."""

from . import brave, duckduckgo, exa, firecrawl, jina, parallel, perplexity, serpapi, tavily

SEARCH = {
    "brave": brave.search,
    "duckduckgo": duckduckgo.search,
    "exa": exa.search,
    "firecrawl": firecrawl.search,
    "jina": jina.search,
    "parallel": parallel.search,
    "perplexity": perplexity.search,
    "serpapi": serpapi.search,
    "tavily": tavily.search,
}
SCRAPE = {
    "exa": exa.scrape,
    "firecrawl": firecrawl.scrape,
    "jina": jina.scrape,
    "parallel": parallel.scrape,
    "tavily": tavily.scrape,
}
