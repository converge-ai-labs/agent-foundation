<best-practices>
- Use HEAD requests for existence or metadata before downloading when size or type affects the next step.
- Use `download` for large files, binary files, PDFs, or content that must be kept locally.
- Use `scrape` for Web-page text extraction.
- Do not treat fetched text snippets as a substitute for exact downloaded bytes.
</best-practices>
