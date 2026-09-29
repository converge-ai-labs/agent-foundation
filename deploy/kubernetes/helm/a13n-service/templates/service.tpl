# Control serves the API and Console; workers accept no traffic.
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}-control
  {{- with .Values.service.labels }}
  labels:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with .Values.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: {{ .Values.service.type }}
  selector:
    {{- include "a13n.selector" . | nindent 4 }}
    app.kubernetes.io/component: control
  ports:
    - name: http
      port: 8000
      targetPort: http
      {{- if eq .Values.service.type "NodePort" }}
      nodePort: {{ .Values.service.nodePort }}
      {{- end }}
{{- if .Values.serviceAccount.create }}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "a13n.serviceAccountName" . | quote }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
automountServiceAccountToken: false
{{- end }}
