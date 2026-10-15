# --wait keeps post-install/post-upgrade hooks after both Deployments are ready on the new image.
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "a13n.name" . }}-refresh-agents-{{ .Release.Revision }}
  annotations:
    helm.sh/hook: post-install,post-upgrade
    helm.sh/hook-delete-policy: before-hook-creation
spec:
  backoffLimit: 2
  activeDeadlineSeconds: 600
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        {{- include "a13n.selector" . | nindent 8 }}
        app.kubernetes.io/component: refresh-agents
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- include "a13n.podSpec" . | nindent 6 }}
      restartPolicy: Never
      containers:
        - name: refresh-agents
          {{- include "a13n.container" . | nindent 10 }}
          args: ["a13n-service", "--config", "/app/service.toml", "refresh-builtin-agents"]
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
      volumes:
        {{- include "a13n.volumes" . | nindent 8 }}
