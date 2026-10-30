{{- if .Values.monitoring.vmagent.enabled }}
{{- $vm := .Values.monitoring.vmagent }}
{{- $writer := merge (dict "monitoringWriter" true) . }}
{{- $name := printf "%s-monitoring" (include "a13n.name" .) }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $name }}
data:
  scrape.yml: |
    scrape_config_files:
      - /var/lib/a13n-monitoring/fragments/a13n-managed.yml
---
{{- if not $vm.persistence.existingClaim }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ $name }}
  annotations:
    helm.sh/resource-policy: keep
spec:
  accessModes: [ReadWriteOnce]
  {{- if ne $vm.persistence.storageClassName nil }}
  storageClassName: {{ $vm.persistence.storageClassName | quote }}
  {{- end }}
  resources:
    requests:
      storage: {{ $vm.persistence.size }}
---
{{- end }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      {{- include "a13n.selector" . | nindent 6 }}
      app.kubernetes.io/component: monitoring
  template:
    metadata:
      labels:
        {{- include "a13n.selector" . | nindent 8 }}
        app.kubernetes.io/component: monitoring
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.tpl") . | sha256sum }}
    spec:
      {{- include "a13n.podSpec" . | nindent 6 }}
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      initContainers:
        - name: wait-for-schema
          {{- include "a13n.container" . | nindent 10 }}
          command: ["sh", "-ec"]
          args:
            - until a13n-service --config /app/service.toml migrate --check; do sleep 5; done
          resources:
            {{- toYaml (default .Values.resources $vm.writerResources) | nindent 12 }}
        - name: prepare-collector
          {{- include "a13n.container" $writer | nindent 10 }}
          command: ["python", "-c"]
          args:
            - |
              import fcntl, os
              from pathlib import Path
              root = Path('/var/lib/a13n-monitoring')
              for name in ('fragments', 'queue'):
                  (root / name).mkdir(mode=0o700, exist_ok=True)
                  (root / name).chmod(0o700)
              # A fresh Pod starts empty; the writer rediscovers current targets.
              # Keep the lock inode and pending forwarding queue across replacements.
              lock = os.open(root / 'fragments/.a13n-vmagent.lock', os.O_CREAT | os.O_RDWR, 0o600)
              fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
              path = root / 'fragments/a13n-managed.yml'
              fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
              with os.fdopen(fd, 'w') as stream:
                  stream.write('[]\n')
              path.chmod(0o600)
              os.close(lock)
          resources:
            {{- toYaml (default .Values.resources $vm.writerResources) | nindent 12 }}
      containers:
        - name: writer
          {{- include "a13n.container" $writer | nindent 10 }}
          args: ["a13n-service", "--config", "/app/service.toml", "run", "--role", "control"]
          env:
            {{- include "a13n.monitoringEnv" $writer | nindent 12 }}
            - name: A13N_SERVER__HOST
              value: "127.0.0.1"
            - name: A13N_SERVER__PORT
              value: "8000"
          startupProbe:
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"]
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 60
          readinessProbe:
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3); urllib.request.urlopen('http://127.0.0.1:8429/health', timeout=3)"]
            periodSeconds: 10
            timeoutSeconds: 10
          livenessProbe:
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
            periodSeconds: 15
            timeoutSeconds: 5
          resources:
            {{- toYaml (default .Values.resources $vm.writerResources) | nindent 12 }}
        - name: vmagent
          {{- if $vm.image.digest }}
          image: {{ printf "%s@%s" $vm.image.repository $vm.image.digest | quote }}
          {{- else }}
          image: {{ printf "%s:%s" $vm.image.repository $vm.image.tag | quote }}
          {{- end }}
          imagePullPolicy: {{ $vm.image.pullPolicy }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [ALL]
          args:
            - -httpListenAddr=127.0.0.1:8429
            - -promscrape.config=/etc/vmagent/scrape.yml
            - {{ printf "-remoteWrite.url=%s" (include "a13n.vmagentRemoteWriteUrl" .) | quote }}
            - -remoteWrite.tmpDataPath=/var/lib/a13n-monitoring/queue
            - {{ printf "-remoteWrite.maxDiskUsagePerURL=%s" $vm.maxDiskUsage | quote }}
            {{- if $vm.remoteWriteBearerTokenSecret.name }}
            - -remoteWrite.bearerTokenFile=/etc/vmagent/token/token
            {{- end }}
            {{- if $vm.remoteWriteCASecret.name }}
            - -remoteWrite.tlsCAFile=/etc/vmagent/ca/ca.crt
            {{- end }}
          volumeMounts:
            - name: scrape
              mountPath: /etc/vmagent/scrape.yml
              subPath: scrape.yml
              readOnly: true
            - name: monitoring
              mountPath: /var/lib/a13n-monitoring/fragments
              subPath: fragments
              readOnly: true
            - name: monitoring
              mountPath: /var/lib/a13n-monitoring/queue
              subPath: queue
            {{- if $vm.remoteWriteBearerTokenSecret.name }}
            - name: remote-write-token
              mountPath: /etc/vmagent/token
              readOnly: true
            {{- end }}
            {{- if $vm.remoteWriteCASecret.name }}
            - name: remote-write-ca
              mountPath: /etc/vmagent/ca
              readOnly: true
            {{- end }}
          resources:
            {{- toYaml $vm.resources | nindent 12 }}
      volumes:
        {{- include "a13n.volumes" . | nindent 8 }}
        - name: monitoring
          persistentVolumeClaim:
            claimName: {{ default $name $vm.persistence.existingClaim }}
        - name: scrape
          configMap:
            name: {{ $name }}
        {{- if $vm.remoteWriteBearerTokenSecret.name }}
        - name: remote-write-token
          secret:
            secretName: {{ $vm.remoteWriteBearerTokenSecret.name | quote }}
            defaultMode: 0440
            items:
              - key: {{ $vm.remoteWriteBearerTokenSecret.key | quote }}
                path: token
        {{- end }}
        {{- if $vm.remoteWriteCASecret.name }}
        - name: remote-write-ca
          secret:
            secretName: {{ $vm.remoteWriteCASecret.name | quote }}
            defaultMode: 0440
            items:
              - key: {{ $vm.remoteWriteCASecret.key | quote }}
                path: ca.crt
        {{- end }}
{{- end }}
