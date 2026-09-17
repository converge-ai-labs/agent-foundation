{{- if .Values.postgresql.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}-postgres
spec:
  clusterIP: None
  selector:
    app.kubernetes.io/name: a13n-postgres
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - port: 5432
      targetPort: postgres
      name: postgres
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "a13n.name" . }}-postgres
spec:
  serviceName: {{ include "a13n.name" . }}-postgres
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: a13n-postgres
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: a13n-postgres
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      automountServiceAccountToken: false
      containers:
        - name: postgres
          image: {{ .Values.postgresql.image | quote }}
          envFrom:
            - secretRef:
                name: {{ .Values.postgresql.existingSecret }}
          env:
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - name: postgres
              containerPort: 5432
          readinessProbe:
            exec:
              command: [sh, -c, 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
            periodSeconds: 5
          resources:
            {{- toYaml .Values.postgresql.resources | nindent 12 }}
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        {{- if ne .Values.postgresql.storageClassName nil }}
        storageClassName: {{ .Values.postgresql.storageClassName | quote }}
        {{- end }}
        resources:
          requests:
            storage: {{ .Values.postgresql.size }}
{{- end }}
