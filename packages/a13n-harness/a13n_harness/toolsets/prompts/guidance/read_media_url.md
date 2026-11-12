<read-media-url>
- Use this tool to read image, video, or audio input for focused analysis. The tool description lists the URL types available for the current model, configured understanding models, and Run.
- Provide focused instructions for timestamped summaries, transcription, speaker identification, on-screen text, or specific detail extraction.
- Local files use `view`.
</read-media-url>

<read-media-url-inline>
- Direct HTTP(S) media resources are downloaded with bounded bytes; web pages containing media are not direct resources.
- Native input is preferred when the current model supports the kind. Otherwise the configured understanding model returns text. Focused instructions apply to both paths.
- Original images are limited to 100 MiB; audio and video to 16 MiB. Native videos also fit the current model's encoded-byte budget (10 MiB by default), individually and in aggregate per request.
- Oversized resources are rejected; no video compression, splitting, or frame extraction is performed. Image request preparation uses the receiving model's policy.
</read-media-url-inline>

<read-media-url-youtube>
- When the tool description lists YouTube support, YouTube links are attached as native video URL input for the selected transport, without downloading or converting the video.
- Provide the original YouTube URL; do not scrape the watch page or replace it with an extracted download link.
</read-media-url-youtube>
