{{- if and (eq .Values.objects.backend "local") (not .Values.persistence.existingClaim) }}
# Every Service Pod mounts this claim; ReadWriteOnce shares it only among Pods on one node.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "a13n.name" . }}-objects
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
