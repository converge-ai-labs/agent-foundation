{{- if not .Values.persistence.existingClaim }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "a13n.name" . }}-data
  annotations:
    helm.sh/resource-policy: keep
spec:
  accessModes: [ReadWriteOnce]
  {{- if ne .Values.persistence.storageClassName nil }}
  storageClassName: {{ .Values.persistence.storageClassName | quote }}
  {{- end }}
  resources:
    requests:
      storage: {{ .Values.persistence.size }}
{{- end }}
