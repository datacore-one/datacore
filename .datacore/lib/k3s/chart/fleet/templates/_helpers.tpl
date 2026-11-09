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
{{- /* Where the agent's Datacore code is: its runner checkout by default, or
       its own Data root (Winston) -- the Hermes gate imports from here. */}}
- {name: DATACORE_LIB, value: {{ $a.datacoreLib | default (printf "%s/.datacore/v2-runner/.datacore/lib" $home) | quote }}}
{{- /* System Python first: the agent's jobs import packages installed for
       /usr/bin/python3. The runtime's own tools come last (pathTail). */}}
- {name: PATH, value: {{ printf "%s/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:%s" $home $a.pathTail | quote }}}
- {name: HTTPS_PROXY, value: {{ $proxy | quote }}}
- {name: HTTP_PROXY, value: {{ $proxy | quote }}}
- {name: https_proxy, value: {{ $proxy | quote }}}
- {name: http_proxy, value: {{ $proxy | quote }}}
{{- /* The API server directly, never through the proxy (an agent with the
       kill switch reaches it; for any other the network policy refuses). */}}
- {name: NO_PROXY, value: "localhost,127.0.0.1,::1,kubernetes.default.svc,10.43.0.1"}
- {name: no_proxy, value: "localhost,127.0.0.1,::1,kubernetes.default.svc,10.43.0.1"}
- {name: NODE_USE_ENV_PROXY, value: "1"}
- {name: TMPDIR, value: /tmp}
{{- end -}}

{{- define "fleet.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
{{- end -}}

{{/* An agent's secret files (secretFiles: [{secret, key, path}]), read-only at their path. */}}
{{- define "fleet.secretFileMounts" -}}
{{- range $i, $f := (.secretFiles | default list) }}
- {name: {{ printf "secret-file-%d" $i }}, mountPath: {{ $f.path }}, subPath: {{ $f.key }}, readOnly: true}
{{- end }}
{{- end }}

{{- define "fleet.secretFileVolumes" -}}
{{- range $i, $f := (.secretFiles | default list) }}
- name: {{ printf "secret-file-%d" $i }}
  secret:
    secretName: {{ $f.secret }}
    items: [{key: {{ $f.key }}, path: {{ $f.key }}}]
    defaultMode: 0444
{{- end }}
{{- end }}

{{- define "fleet.sshMount" -}}
{{- if ne .home .imageHome }}
- {name: home, mountPath: {{ printf "%s/.ssh" .imageHome }}, subPath: .ssh}
{{- end }}
{{- end }}
