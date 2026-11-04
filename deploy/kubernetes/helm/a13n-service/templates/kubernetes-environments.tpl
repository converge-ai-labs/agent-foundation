{{- if .Values.kubernetesEnvironments.enabled }}
{{- $namespace := default .Release.Namespace .Values.kubernetesEnvironments.namespace }}
{{- $name := printf "%s-environments" (include "a13n.name" .) }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "a13n.name" . }}-sandbox
  namespace: {{ $namespace | quote }}
automountServiceAccountToken: false
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ $name }}
  namespace: {{ $namespace | quote }}
rules:
  - apiGroups: [""]
    resources: [pods, secrets, persistentvolumeclaims]
    verbs: [get, list, create, delete]
  - apiGroups: [""]
    resources: [configmaps]
    verbs: [get]
  - apiGroups: [""]
    resources: [events]
    verbs: [list]
  - apiGroups: [""]
    resources: [pods/log]
    verbs: [get]
  - apiGroups: [networking.k8s.io]
    resources: [networkpolicies]
    verbs: [get, list, create, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {{ $name }}
  namespace: {{ $namespace | quote }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {{ $name }}
subjects:
  - kind: ServiceAccount
    name: {{ include "a13n.serviceAccountName" . | quote }}
    namespace: {{ .Release.Namespace | quote }}
{{- end }}

{{/* Native projected credentials rotate in place; only runtime Service containers mount them. */}}
{{- define "a13n.kubernetesVolumes" -}}
{{- if .Values.kubernetesEnvironments.enabled }}
- name: kubernetes-api
  projected:
    defaultMode: 0440
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 3600
      - configMap:
          name: kube-root-ca.crt
          items:
            - key: ca.crt
              path: ca.crt
      - downwardAPI:
          items:
            - path: namespace
              fieldRef:
                fieldPath: metadata.namespace
{{- end }}
{{- end -}}
