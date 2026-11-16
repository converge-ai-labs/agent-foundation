<search>
Use search for discovery. Open authoritative result URLs with `scrape` or `fetch` before relying on extracted snippets.
</search>

<scrape>
- Use complete HTTP or HTTPS URLs.
- Use `download` for binary assets, PDFs, or content whose exact bytes matter.
- Treat scraped Markdown as extracted reading material, not proof of original bytes or layout.
</scrape>

<fetch>
- Use HEAD requests for existence or metadata before downloading when size or type affects the next step.
- Use `download` for large files, binary files, PDFs, or content that must be kept locally.
- Use `scrape` for Web-page text extraction.
- Do not treat fetched text snippets as a substitute for exact downloaded bytes.
</fetch>

<download>
- Use `download` when a local Environment path or complete bytes matter.
- Use `scrape` for Web-page text and `fetch` for quick checks that do not need a saved file.
- For unknown large assets, check metadata first when size or type affects the next step.
</download>
