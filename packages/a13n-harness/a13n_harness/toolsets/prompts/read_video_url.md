<best-practices>
- Use this tool when asked to analyze a video URL; it attaches native video input for the current model, not a textual web page or a downloaded file.
- YouTube links require the model's YouTube URL capability; other video resource URLs require its general video URL capability. A web page containing a video is not a video resource URL.
- Provide focused instructions for timestamped summaries, transcription, speaker identification, on-screen text, or specific detail extraction.
- If a URL is unsupported, use a model with the matching URL capability or obtain a local video file and use `view`. This tool has no download or auxiliary-model fallback.
</best-practices>
