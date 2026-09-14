{{/*
Chart name and version, for the standard app.kubernetes.io/* labels every template below uses.
*/}}
{{- define "astra-data.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "astra-data.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "astra-data.labels" -}}
app.kubernetes.io/name: {{ include "astra-data.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
astra.data/environment: {{ .Values.environment }}
{{- end -}}

{{- define "astra-data.graphSvc.labels" -}}
{{ include "astra-data.labels" . }}
app.kubernetes.io/component: graph-svc
{{- end -}}

{{- define "astra-data.graphSvc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "astra-data.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: graph-svc
{{- end -}}

{{- define "astra-data.consoleWeb.labels" -}}
{{ include "astra-data.labels" . }}
app.kubernetes.io/component: console-web
{{- end -}}

{{- define "astra-data.consoleWeb.selectorLabels" -}}
app.kubernetes.io/name: {{ include "astra-data.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: console-web
{{- end -}}
