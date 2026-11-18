{{- define "a13n.browserService" -}}
{{- printf "%s-browsers" (include "a13n.name" .) -}}
{{- end -}}
{{- define "a13n.browserOwner" -}}
{{- printf "a13n-%s" (printf "%s/%s" .Release.Namespace .Release.Name | sha256sum | trunc 24) -}}
{{- end -}}
{{- if .Values.kubernetesBrowsers.enabled }}
{{- if not (semverCompare ">=1.32.0-0" .Capabilities.KubeVersion.Version) }}
{{- fail "kubernetesBrowsers requires Kubernetes 1.32 or newer" }}
{{- end }}
{{- $namespace := default .Release.Namespace .Values.kubernetesBrowsers.namespace }}
{{- $name := include "a13n.browserService" . }}
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ $name }}
  namespace: {{ $namespace | quote }}
rules:
  - apiGroups: [apps]
    resources: [statefulsets]
    verbs: [get, create, patch, delete]
  - apiGroups: [""]
    resources: [pods, persistentvolumeclaims]
    verbs: [get]
  - apiGroups: [""]
    resources: [secrets]
    verbs: [get, create]
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
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  namespace: {{ $namespace | quote }}
spec:
  clusterIP: None
  selector:
    browsers.a13n.dev/owner: {{ include "a13n.browserOwner" . }}
  ports:
    - name: cdp
      port: 9222
    - name: viewer
      port: 6081
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $name }}
  namespace: {{ $namespace | quote }}
spec:
  podSelector:
    matchLabels:
      browsers.a13n.dev/owner: {{ include "a13n.browserOwner" . }}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace | quote }}
          podSelector:
            matchLabels:
              {{- include "a13n.selector" . | nindent 14 }}
            matchExpressions:
              - key: app.kubernetes.io/component
                operator: In
                values: [control, worker]
      ports:
        - { protocol: TCP, port: 9222 }
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace | quote }}
          podSelector:
            matchLabels:
              {{- include "a13n.selector" . | nindent 14 }}
              app.kubernetes.io/component: control
      ports:
        - { protocol: TCP, port: 6081 }
{{- end }}
