{{- if and .Values.metrics.enabled .Values.metrics.podMonitor.enabled }}
# Scrapes control and worker Pods alike; workers have no Service to monitor.
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: {{ include "a13n.name" . }}
  {{- with .Values.metrics.podMonitor.labels }}
  labels:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  # The job is named a13n-service, the name the alert rules and dashboards in deploy/monitoring select.
  jobLabel: app.kubernetes.io/name
  selector:
    matchLabels:
      {{- include "a13n.selector" . | nindent 6 }}
  podMetricsEndpoints:
    - port: metrics
      path: /metrics
      interval: {{ .Values.metrics.podMonitor.interval }}
{{- end }}
