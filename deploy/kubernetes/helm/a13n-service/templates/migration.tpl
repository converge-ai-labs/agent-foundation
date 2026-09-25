# One Job per release revision: Job templates are immutable, and every upgrade migrates before its replicas start.
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "a13n.name" . }}-migrate-{{ .Release.Revision }}
spec:
  backoffLimit: 10
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        {{- include "a13n.selector" . | nindent 8 }}
        app.kubernetes.io/component: migrate
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- include "a13n.podSpec" . | nindent 6 }}
      restartPolicy: OnFailure
      containers:
        - name: migrate
          {{- include "a13n.container" . | nindent 10 }}
          args: ["a13n-service", "--config", "/app/service.toml", "migrate"]
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
      volumes:
        {{- include "a13n.volumes" . | nindent 8 }}
