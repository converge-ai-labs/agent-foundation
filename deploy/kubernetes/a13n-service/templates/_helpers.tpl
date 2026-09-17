{{- define "a13n.name" -}}
{{- printf "%s-a13n" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "a13n.selector" -}}
app.kubernetes.io/name: a13n-service
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "a13n.validate" -}}
{{- if not (has .Values.profile (list "local" "distributed")) -}}
{{- fail "profile must be local or distributed" -}}
{{- end -}}
{{- if lt (int .Values.replicaCount) 1 -}}
{{- fail "replicaCount must be positive" -}}
{{- end -}}
{{- if and (eq .Values.profile "local") (ne (int .Values.replicaCount) 1) -}}
{{- fail "local storage requires exactly one replica" -}}
{{- end -}}
{{- if eq .Values.profile "distributed" -}}
{{- if .Values.redis.enabled -}}
{{- fail "bundled Redis is for local development only" -}}
{{- end -}}
{{- if .Values.postgresql.enabled -}}
{{- fail "bundled PostgreSQL is for local development only" -}}
{{- end -}}
{{- $_ := required "distributed profile requires objects.bucket" .Values.objects.bucket -}}
{{- $_ := required "distributed profile requires persistence.existingClaim (RWX)" .Values.persistence.existingClaim -}}
{{- if not (hasPrefix "https://" .Values.publicOrigin) -}}
{{- fail "distributed publicOrigin must use HTTPS" -}}
{{- end -}}
{{- end -}}
{{- $_ := required "existingSecret is required" .Values.existingSecret -}}
{{- end -}}
