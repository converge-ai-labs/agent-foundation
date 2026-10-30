<best-practices>
- Direct HTTP(S) media resources are downloaded with bounded bytes; web pages containing media are not direct resources.
- Native input is preferred when the current model supports the kind. Otherwise the configured understanding model returns text. Focused instructions apply to both paths.
- Original images are limited to 100 MiB; audio and video to 16 MiB. Native videos also fit the current model's encoded-byte budget (10 MiB by default), individually and in aggregate per request.
- Oversized resources are rejected; no video compression, splitting, or frame extraction is performed. Image request preparation uses the receiving model's policy.
</best-practices>
