{{- define "ar.name" -}}agent-runtime{{- end -}}

{{- define "ar.labels" -}}
app.kubernetes.io/name: {{ include "ar.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ar.selector" -}}
app: {{ include "ar.name" . }}
{{- end -}}

{{- define "ar.saName" -}}
{{- if .Values.serviceAccount.create -}}{{ .Values.serviceAccount.name }}{{- else -}}default{{- end -}}
{{- end -}}

{{/* AnalysisTemplate names used by both strategies. */}}
{{- define "ar.analysisTemplates" -}}
- templateName: agent-eval-score
- templateName: agent-latency-p95
- templateName: agent-cost-per-task
- templateName: agent-error-rate
{{- end -}}

{{- define "ar.analysisArgs" -}}
- name: track
  value: {{ .track }}
- name: agent-version
  value: {{ .version | quote }}
{{- end -}}
