{{/*
  installer umbrella — helpers (adapted from krateo-openstack-blueprint osh.* helpers).
  Unlike openstack, each component pins its OWN chart version, so the served CRD
  apiVersion is derived per-component from that component's version.
*/}}

{{/* Served apiVersion for a component's generated CRD, from its version string.
     "0.22.2" -> "composition.krateo.io/v0-22-2" */}}
{{- define "inst.apiVersion" -}}
{{- $ver := index . 0 -}}
{{- printf "composition.krateo.io/v%s" ($ver | toString | replace "." "-") -}}
{{- end -}}

{{/* Is a feature flag enabled? args: (list $ "featureName"); empty featureName => true */}}
{{- define "inst.featureEnabled" -}}
{{- $top := index . 0 -}}{{- $feat := index . 1 -}}
{{- if not $feat -}}true{{- else -}}
{{- if index $top.Values.features $feat -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/* Does the component's generated CRD exist yet? args: (list "Kind") */}}
{{- define "inst.crdExists" -}}
{{- $kind := index . 0 -}}{{- $found := "" -}}
{{- range (lookup "apiextensions.k8s.io/v1" "CustomResourceDefinition" "" "").items -}}
{{- if and (eq .spec.group "composition.krateo.io") (eq .spec.names.kind $kind) -}}{{- $found = "true" -}}{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}

{{/* Is a peer Composition Ready=True? args: (list $ "Kind" "name" "version") */}}
{{- define "inst.ready" -}}
{{- $top := index . 0 -}}{{- $kind := index . 1 -}}{{- $name := index . 2 -}}{{- $ver := index . 3 -}}
{{- $apiv := include "inst.apiVersion" (list $ver) -}}
{{- $o := lookup $apiv $kind $top.Values.namespaces.krateo $name -}}
{{- $r := "" -}}
{{- if $o -}}
{{- range ($o.status.conditions | default list) -}}
{{- if and (eq .type "Ready") (eq (.status | toString) "True") -}}{{- $r = "true" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $r -}}
{{- end -}}

{{/* Are all of a component's deps Ready? args: (list $ $deps $components) */}}
{{- define "inst.depsReady" -}}
{{- $top := index . 0 -}}{{- $deps := index . 1 -}}{{- $comps := index . 2 -}}
{{- $all := "true" -}}
{{- range $d := $deps -}}
  {{- $kind := "" -}}{{- $ver := "" -}}
  {{- range $c := $comps -}}{{- if eq $c.name $d -}}{{- $kind = $c.kind -}}{{- $ver = $c.version -}}{{- end -}}{{- end -}}
  {{- if ne (include "inst.ready" (list $top $kind $d $ver)) "true" -}}{{- $all = "" -}}{{- end -}}
{{- end -}}
{{- $all -}}
{{- end -}}
