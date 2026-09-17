{{- $root := . -}}
{{- $roles := dict "all" (dict "replicaCount" .Values.replicaCount "resources" .Values.resources) -}}
{{- if eq .Values.profile "distributed" -}}
{{- $roles = .Values.roles -}}
{{- end -}}
{{- range $role, $settings := $roles }}
{{- with $root }}
{{- $name := include "a13n.name" . -}}
{{- if ne $role "all" -}}
{{- $name = printf "%s-%s" $name $role -}}
{{- end }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
spec:
  replicas: {{ $settings.replicaCount }}
  strategy:
    {{- if eq .Values.profile "local" }}
    type: Recreate
    {{- else }}
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
    {{- end }}
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
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "a13n.name" . }}
      terminationGracePeriodSeconds: 120
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if has $role (list "worker" "connectivity") }}
      initContainers:
        - name: wait-for-schema
          image: {{ printf "%s:%s" .Values.image.repository .Values.image.tag | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["sh", "-ec"]
          args:
            - |
              attempt=0
              until a13n-service --config /app/service.toml db current --check-heads; do
                attempt=$((attempt + 1))
                if [ "$attempt" -ge 420 ]; then
                  echo "Schema is not ready; inspect Control migration logs." >&2
                  exit 1
                fi
                sleep 5
              done
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
          envFrom:
            - secretRef:
                name: {{ .Values.existingSecret }}
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
          volumeMounts:
            - name: config
              mountPath: /app/service.toml
              subPath: service.toml
              readOnly: true
      {{- end }}
      containers:
        - name: service
          image: {{ printf "%s:%s" .Values.image.repository .Values.image.tag | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["a13n-service", "--config", "/app/service.toml", "serve", "--role", {{ $role | quote }}]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
          env:
            - name: A13N_SERVICE_AUTO_MIGRATE
              value: {{ or (eq $role "all") (eq $role "control") | quote }}
          envFrom:
            - secretRef:
                name: {{ .Values.existingSecret }}
          ports:
            - name: http
              containerPort: 8000
          startupProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 420
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            timeoutSeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            timeoutSeconds: 5
            periodSeconds: 15
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
          volumeMounts:
            - name: config
              mountPath: /app/service.toml
              subPath: service.toml
              readOnly: true
            - name: data
              mountPath: /app/var
      volumes:
        - name: config
          configMap:
            name: {{ include "a13n.name" . }}-config
        - name: data
          persistentVolumeClaim:
            claimName: {{ default (printf "%s-data" (include "a13n.name" .)) .Values.persistence.existingClaim }}
{{- end }}
{{- end }}
