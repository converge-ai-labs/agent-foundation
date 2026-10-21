{{- define "a13n.monitoringEnv" -}}
{{- if .Values.monitoring.vmagent.enabled }}
# Managed mode owns this map; other replicas never write collector files.
- name: A13N_MONITORING__ENABLED
  value: "true"
- name: A13N_MONITORING__COLLECTORS
  value: {{ dict "vmagent" (dict "directory" "/var/lib/a13n-monitoring/fragments" "endpoint" "http://127.0.0.1:8429" "allow_http_reload" true "writer" (default false .monitoringWriter)) | toJson | quote }}
{{- end }}
{{- with .Values.monitoring.deploymentPreset }}
{{- if and .enabled $.monitoringAdmin }}
- name: A13N_MONITORING__DEPLOYMENT_PRESET
  value: {{ dict "name" .name "collector" .collector "query_endpoint" (required "monitoring.deploymentPreset.queryEndpoint or monitoring.vmagent.prometheusUrl is required" (default $.Values.monitoring.vmagent.prometheusUrl .queryEndpoint)) "workspace_ids" .workspaceIds | toJson | quote }}
{{- with .queryBearerTokenSecret }}
{{- if .name }}
- name: A13N_MONITORING__DEPLOYMENT_QUERY_BEARER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .name | quote }}
      key: {{ .key | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "a13n.vmagentRemoteWriteUrl" -}}
{{- with .Values.monitoring.vmagent -}}
{{- if .remoteWriteUrl -}}
{{- .remoteWriteUrl -}}
{{- else -}}
{{- printf "%s/api/v1/write" (trimSuffix "/" (required "monitoring.vmagent.remoteWriteUrl or prometheusUrl is required" .prometheusUrl)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
