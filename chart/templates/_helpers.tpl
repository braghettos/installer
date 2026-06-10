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

{{/* Is a feature flag enabled? args: (list $ "featureName"); empty featureName => true.
     Krateo CORE platform modules (the engine + the composable portal: authn/snowplow/
     frontend/portal) install REGARDLESS of their flag — they are not optional. Only the
     add-on features (observability, oasgen, githubMcp, podRestartAlert) are gated. */}}
{{- define "inst.coreFeatures" -}}composableoperations composableportal composableportalstarter{{- end -}}
{{- define "inst.featureEnabled" -}}
{{- $top := index . 0 -}}{{- $feat := index . 1 -}}
{{- if not $feat -}}true
{{- else if has $feat (splitList " " (include "inst.coreFeatures" .)) -}}true
{{- else if index $top.Values.features $feat -}}true{{- end -}}
{{- end -}}

{{/* Does the component's generated CRD exist AND serve this component's version yet?
     args: (list "Kind" "version")
     Version-aware: core-provider derives the served apiVersion from the chart version
     ("0.1.1" -> "v0-1-1"). On a component version bump the Kind already exists but the
     new served version lags until core-provider regenerates the CRD; a typed lookup of
     the not-yet-served apiVersion is a HARD ERROR. Checking spec.versions[].served for
     the exact version makes both Pass B emission and readiness checks tolerate that
     transient (treat as "not ready yet") instead of failing the whole render. */}}
{{- define "inst.crdExists" -}}
{{- $kind := index . 0 -}}{{- $ver := index . 1 -}}
{{- $want := printf "v%s" ($ver | toString | replace "." "-") -}}
{{- $found := "" -}}
{{- range (lookup "apiextensions.k8s.io/v1" "CustomResourceDefinition" "" "").items -}}
{{- if and (eq .spec.group "composition.krateo.io") (eq .spec.names.kind $kind) -}}
{{- range .spec.versions -}}{{- if and (eq .name $want) .served -}}{{- $found = "true" -}}{{- end -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}

{{/* Is a peer Composition Ready=True? args: (list $ "Kind" "name" "version")
     Guarded: a typed lookup of a Kind whose CRD is not yet registered is a HARD ERROR
     in chart-inspector (server-side), unlike client-side `helm template` which returns
     empty. So short-circuit via inst.crdExists (an apiextensions lookup, always valid)
     before doing the typed Composition lookup. */}}
{{- define "inst.ready" -}}
{{- $top := index . 0 -}}{{- $kind := index . 1 -}}{{- $name := index . 2 -}}{{- $ver := index . 3 -}}
{{- $r := "" -}}
{{- if eq (include "inst.crdExists" (list $kind $ver)) "true" -}}
{{- $apiv := include "inst.apiVersion" (list $ver) -}}
{{- $o := lookup $apiv $kind $top.Values.namespaces.krateo $name -}}
{{- if $o -}}
{{- range ($o.status.conditions | default list) -}}
{{- if and (eq .type "Ready") (eq (.status | toString) "True") -}}{{- $r = "true" -}}{{- end -}}
{{- end -}}
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

{{/* External IP of a browser-facing component's LoadBalancer Service. args: (list $ "svcNameSubstring")
     The component's underlying Service is named after its Helm release (e.g. authn-<hash>), so we
     match on a stable substring and return the assigned ingress IP — "" until the cloud LB is ready.
     This is the reconcile-time resolution the values.yaml exposure model documents: each reconcile
     re-runs the lookup, so the frontend config fills in as soon as the peer IPs are assigned. */}}
{{- define "inst.lbip" -}}
{{- $top := index . 0 -}}{{- $sub := index . 1 -}}{{- $ip := "" -}}
{{- range (lookup "v1" "Service" $top.Values.namespaces.krateo "").items -}}
{{- if and (eq (.spec.type | toString) "LoadBalancer") (contains $sub .metadata.name) -}}
{{- range (.status.loadBalancer.ingress | default list) -}}{{- if .ip -}}{{- $ip = .ip -}}{{- end -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- $ip -}}
{{- end -}}
