{{- include "a13n.validate" . -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "a13n.name" . }}-config
data:
  service.toml: |
    [deployment]
    mode = {{ ternary "single_host" "distributed" (eq .Values.profile "local") | quote }}
    [service]
    role = {{ ternary "all" "control" (eq .Values.profile "local") | quote }}
    host = "0.0.0.0"
    port = 8000
    deployment_environment_name = {{ .Values.environmentName | toJson }}
    [redis]
    backend = "redis"
    {{- if .Values.redis.enabled }}
    url = {{ printf "redis://%s-redis:6379/0" (include "a13n.name" .) | toJson }}
    {{- end }}
    [objects]
    backend = {{ ternary "local" "s3" (eq .Values.profile "local") | quote }}
    local_root = "/app/var/objects"
    {{- if eq .Values.profile "distributed" }}
    bucket = {{ .Values.objects.bucket | toJson }}
    {{- if .Values.objects.region }}
    region = {{ .Values.objects.region | toJson }}
    {{- end }}
    {{- if .Values.objects.endpointUrl }}
    endpoint_url = {{ .Values.objects.endpointUrl | toJson }}
    {{- end }}
    force_path_style = {{ .Values.objects.forcePathStyle }}
    {{- end }}
    [filesystem]
    root = "/app/var/files"
    [migration]
    auto_migrate = false
    [worker]
    drain_seconds = 30
    [logging]
    format = "json"
    [connectivity]
    public_origin = {{ .Values.publicOrigin | toJson }}
    {{- if and (eq .Values.profile "local") (hasPrefix "http://" .Values.publicOrigin) }}
    http_origins = [{{ .Values.publicOrigin | toJson }}]
    {{- end }}
    [iam]
    public_origin = {{ .Values.publicOrigin | toJson }}
    {{- with .Values.extraConfig }}
    {{- . | nindent 4 }}
    {{- end }}
