{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "a13n.name" . }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- with .Values.ingress.className }}
  ingressClassName: {{ . | quote }}
  {{- end }}
  {{- with .Values.ingress.tls }}
  tls:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          {{- if eq .Values.profile "distributed" }}
          - path: /connectivity/v1
            pathType: Prefix
            backend:
              service:
                name: {{ include "a13n.name" . }}-connectivity
                port:
                  number: 8000
          {{- end }}
          {{- range .Values.ingress.paths }}
          - path: {{ . | quote }}
            pathType: Prefix
            backend:
              service:
                name: {{ include "a13n.name" $ }}{{ if $.Values.console.enabled }}-console{{ else if eq $.Values.profile "distributed" }}-control{{ end }}
                port:
                  number: 8000
          {{- end }}
{{- end }}
