FROM ghcr.io/converge-ai-labs/a13n-sandbox@sha256:85ff3fb681a0e5240d0359c21ea11619d3e6efc2394d842a8cd2b41b8ddd723b
COPY --chmod=0755 launch.sh /usr/local/bin/a13n-cloudflare-launch
ENTRYPOINT ["tini", "--", "a13n-cloudflare-launch"]
CMD ["a13n-envd"]
