{{- $root := . -}}
{{- $roles := list "all" -}}
{{- if eq .Values.profile "distributed" -}}
{{- $roles = list "control" "worker" "connectivity" -}}
{{- end -}}
{{- range $role := $roles }}
{{- with $root }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}{{ if ne $role "all" }}-{{ $role }}{{ end }}
  {{- if ne $role "worker" }}
  {{- with .Values.serviceAnnotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
spec:
  type: ClusterIP
  selector:
    {{- include "a13n.selector" . | nindent 4 }}
    app.kubernetes.io/component: {{ $role }}
  ports:
    - name: http
      port: 8000
      targetPort: http
{{- end }}
{{- end }}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "a13n.name" . }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
automountServiceAccountToken: false
