// Qualification instrumentation: installation-only ZIP code must not load
// while serving text-memory requests. This is evidence, not a security sandbox.
const Module=require('node:module');const load=Module._load;
Module._load=function(id,...args){if(id==='adm-zip'||String(id).includes('/adm-zip/'))throw Error('installation-only archive dependency reached');return load.call(this,id,...args)};
