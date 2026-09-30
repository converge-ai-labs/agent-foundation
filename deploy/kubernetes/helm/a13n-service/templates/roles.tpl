{{- /* Each role: its Deployment, disruption budget and optional autoscaler. */ -}}
{{- range $role := list "control" "worker" }}
{{- with $ }}
{{- $settings := index .Values.roles $role }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "a13n.name" . }}-{{ $role }}
spec:
  {{- if not $settings.autoscaling.enabled }}
  replicas: {{ $settings.replicaCount }}
  {{- end }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      {{- include "a13n.selector" . | nindent 6 }}
      app.kubernetes.io/component: {{ $role }}
  template:
    metadata:
      labels:
        {{- include "a13n.selector" . | nindent 8 }}
        app.kubernetes.io/component: {{ $role }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.tpl") . | sha256sum }}
        {{- if .Values.metrics.enabled }}
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ .Values.metrics.port | quote }}
        prometheus.io/path: /metrics
        {{- end }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      {{- include "a13n.podSpec" . | nindent 6 }}
      # Cover the full shutdown sequence, including background work after HTTP requests finish.
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      initContainers:
        # Replicas never migrate: wait until the migration Job has brought the schema to this image's head.
        - name: wait-for-schema
          {{- include "a13n.container" . | nindent 10 }}
          command: ["sh", "-ec"]
          args:
            - until a13n-service --config /app/service.toml migrate --check; do sleep 5; done
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
      containers:
        - name: service
          {{- include "a13n.container" . | nindent 10 }}
          args: ["a13n-service", "--config", "/app/service.toml", "run", "--role", {{ $role | quote }}]
          {{- if .Values.metrics.enabled }}
          # A setting of its own, so a [telemetry] section in extraConfig still applies.
          env:
            - name: A13N_TELEMETRY__METRICS_PORT
              value: {{ .Values.metrics.port | quote }}
          {{- end }}
          ports:
            - name: http
              containerPort: 8000
            {{- if .Values.metrics.enabled }}
            - name: metrics
              containerPort: {{ .Values.metrics.port }}
            {{- end }}
          startupProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 60
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 15
            timeoutSeconds: 5
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
      volumes:
        {{- include "a13n.volumes" . | nindent 8 }}
---
# Voluntary disruptions such as node drains evict one Pod of a role at a time; a single replica stays evictable.
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "a13n.name" . }}-{{ $role }}
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      {{- include "a13n.selector" . | nindent 6 }}
      app.kubernetes.io/component: {{ $role }}
{{- with $settings.autoscaling }}
{{- if .enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "a13n.name" $ }}-{{ $role }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "a13n.name" $ }}-{{ $role }}
  minReplicas: {{ .minReplicas }}
  maxReplicas: {{ .maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .targetCPUUtilizationPercentage }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
