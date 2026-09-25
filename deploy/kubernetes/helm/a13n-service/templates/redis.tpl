{{- if .Values.redis.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}-redis
spec:
  clusterIP: None
  selector:
    app.kubernetes.io/name: a13n-redis
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - name: redis
      port: 6379
      targetPort: redis
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "a13n.name" . }}-redis
spec:
  serviceName: {{ include "a13n.name" . }}-redis
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: a13n-redis
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: a13n-redis
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: redis
          image: {{ .Values.redis.image | quote }}
          # Trusted local development only; there is no public port or ingress.
          args: [redis-server, --appendonly, "yes", --appendfsync, everysec, --bind, 0.0.0.0, --protected-mode, "no"]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
          ports:
            - name: redis
              containerPort: 6379
          startupProbe:
            exec:
              command: [redis-cli, ping]
            periodSeconds: 5
            failureThreshold: 60
            timeoutSeconds: 3
          readinessProbe:
            exec:
              command: [redis-cli, ping]
            periodSeconds: 5
            timeoutSeconds: 3
          livenessProbe:
            exec:
              command: [redis-cli, ping]
            periodSeconds: 15
            timeoutSeconds: 3
          resources:
            {{- toYaml .Values.redis.resources | nindent 12 }}
          volumeMounts:
            - name: data
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        {{- if ne .Values.redis.storageClassName nil }}
        storageClassName: {{ .Values.redis.storageClassName | quote }}
        {{- end }}
        resources:
          requests:
            storage: {{ .Values.redis.size }}
{{- end }}
