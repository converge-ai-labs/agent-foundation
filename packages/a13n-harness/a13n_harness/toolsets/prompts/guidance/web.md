<search>
Discover relevant pages first. Check authoritative sources before relying on extracted snippets.
</search>

<scrape>
- Use complete HTTP or HTTPS URLs.
- Use original bytes for binary assets, PDFs, or content whose exact bytes matter.
- Treat scraped Markdown as extracted reading material, not proof of original bytes or layout.
</scrape>

<fetch>
- Use HEAD requests for existence or metadata when size or type affects the next step.
- Request a snippet only when its bounded text is enough.
- Do not treat fetched text snippets as a substitute for exact downloaded bytes.
</fetch>

<download>
- Use `download` when a local Environment path or complete bytes matter.
- For unknown large assets, check metadata first when size or type affects the next step.
</download>
