<best-practices>
- Use this tool to analyze a video URL. Direct HTTP(S) video resources are downloaded into bounded inline video input; a web page containing video is not a video resource.
- YouTube links require explicit native YouTube URL support in the selected transport. Unsupported YouTube links are rejected, never downloaded or converted.
- Inline videos must fit the configured Base64-after budget (10 MiB by default), both individually and in aggregate per model request. Oversized resources are rejected; no compression, splitting, or frame extraction is performed.
- Provide focused instructions for timestamped summaries, transcription, speaker identification, on-screen text, or specific detail extraction.
- Local files use `view`; the same inline video budget applies before dispatch.
</best-practices>
