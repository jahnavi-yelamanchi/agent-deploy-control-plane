{{- define "rc.labels" -}}
app.kubernetes.io/part-of: adcp
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "rc.env" -}}
{{- range $k, $v := .Values.env }}
- {name: {{ $k }}, value: {{ $v | quote }}}
{{- end }}
- {name: ARGO_ROLLOUTS_NAMESPACE, value: {{ .Values.rolloutNamespace | quote }}}
- {name: ARGO_ROLLOUT_NAME, value: {{ .Values.rolloutName | quote }}}
{{- end -}}

{{- define "rc.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: [ALL]}
{{- end -}}
