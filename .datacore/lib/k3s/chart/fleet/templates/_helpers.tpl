{{- define "fleet.proxyUrl" -}}
http://egress.{{ .Values.sharedNamespace }}.svc.cluster.local:3128
{{- end -}}

{{/* Environment every agent container gets: the proxy, and the agent's own paths. */}}
{{- define "fleet.agentEnv" -}}
{{- $root := index . 0 -}}
{{- $home := $root.Values.defaults.home -}}
{{- $proxy := include "fleet.proxyUrl" $root -}}
- {name: HOME, value: {{ $home | quote }}}
- {name: HERMES_HOME, value: {{ printf "%s/.hermes" $home | quote }}}
- {name: VIRTUAL_ENV, value: /opt/hermes-agent/venv}
- {name: DATACORE_LIB, value: {{ printf "%s/.datacore/v2-runner/.datacore/lib" $home | quote }}}
{{- /* System Python first: the agent's jobs import packages installed for
       /usr/bin/python3. Hermes is started by absolute path and its `hermes`
       command is still found at the end. */}}
- {name: PATH, value: {{ printf "%s/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/node/bin:/opt/hermes-agent/venv/bin" $home | quote }}}
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
