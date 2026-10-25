{{- define "fleet.proxyUrl" -}}
http://egress.{{ .Values.sharedNamespace }}.svc.cluster.local:3128
{{- end -}}

{{/* Environment every agent container gets: the proxy, the agent's own paths and its runtime's. */}}
{{- define "fleet.agentEnv" -}}
{{- $root := index . 0 -}}
{{- $a := index . 1 -}}
{{- $home := $a.home -}}
{{- $proxy := include "fleet.proxyUrl" $root -}}
- {name: HOME, value: {{ $home | quote }}}
{{- range $a.env }}
- {name: {{ .name }}, value: {{ .value | replace "$(HOME)" $home | quote }}}
{{- end }}
- {name: DATACORE_LIB, value: {{ printf "%s/.datacore/v2-runner/.datacore/lib" $home | quote }}}
{{- /* System Python first: the agent's jobs import packages installed for
       /usr/bin/python3. The runtime's own tools come last (pathTail). */}}
- {name: PATH, value: {{ printf "%s/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:%s" $home $a.pathTail | quote }}}
- {name: HTTPS_PROXY, value: {{ $proxy | quote }}}
- {name: HTTP_PROXY, value: {{ $proxy | quote }}}
- {name: https_proxy, value: {{ $proxy | quote }}}
- {name: http_proxy, value: {{ $proxy | quote }}}
- {name: NO_PROXY, value: "localhost,127.0.0.1,::1"}
- {name: no_proxy, value: "localhost,127.0.0.1,::1"}
- {name: NODE_USE_ENV_PROXY, value: "1"}
- {name: TMPDIR, value: /tmp}
{{- end -}}

{{- define "fleet.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
{{- end -}}

{{- define "fleet.sshMount" -}}
{{- if ne .home .imageHome }}
- {name: home, mountPath: {{ printf "%s/.ssh" .imageHome }}, subPath: .ssh}
{{- end }}
{{- end }}
