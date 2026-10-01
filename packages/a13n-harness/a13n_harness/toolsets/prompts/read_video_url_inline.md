<best-practices>
- Direct HTTP(S) video resources are downloaded into bounded inline video input; a web page containing video is not a video resource.
- Inline videos must fit the configured Base64-after budget (10 MiB by default), both individually and in aggregate per model request. The same budget applies to local video input from `view`.
- Oversized resources are rejected; no compression, splitting, or frame extraction is performed.
</best-practices>
