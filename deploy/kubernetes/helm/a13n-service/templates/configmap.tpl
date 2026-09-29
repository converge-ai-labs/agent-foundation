{{- include "a13n.validate" . -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "a13n.name" . }}-config
data:
  service.toml: |
    [server]
    host = "0.0.0.0"
    port = 8000
    public_url = {{ .Values.publicUrl | toJson }}
    {{- with .Values.trustedProxies }}
    trusted_proxies = {{ toJson . }}
    {{- end }}
    [database]
    # The migration Job owns schema changes.
    auto_migrate = false
    {{- if .Values.redis.enabled }}
    [redis]
    url = {{ printf "redis://%s-redis:6379/0" (include "a13n.name" .) | toJson }}
    {{- end }}
    [objects]
    backend = {{ .Values.objects.backend | toJson }}
    {{- if eq .Values.objects.backend "local" }}
    root = "/app/var/objects"
    {{- else }}
    bucket = {{ .Values.objects.bucket | toJson }}
    {{- with .Values.objects.prefix }}
    prefix = {{ toJson . }}
    {{- end }}
    {{- with .Values.objects.region }}
    region = {{ toJson . }}
    {{- end }}
    {{- with .Values.objects.endpointUrl }}
    endpoint_url = {{ toJson . }}
    {{- end }}
    {{- with .Values.objects.addressingStyle }}
    addressing_style = {{ toJson . }}
    {{- end }}
    {{- end }}
    {{- with .Values.extraConfig }}
    {{- . | nindent 4 }}
    {{- end }}
