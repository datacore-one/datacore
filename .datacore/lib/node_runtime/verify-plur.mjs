import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const sdk=process.argv[2];
const {Client}=await import(pathToFileURL(path.join(sdk,'dist/esm/client/index.js')));
const {StdioClientTransport}=await import(pathToFileURL(path.join(sdk,'dist/esm/client/stdio.js')));
const cwd=path.dirname(new URL(import.meta.url).pathname);
const root=await mkdtemp(path.join(tmpdir(),'plur-installed-'));
const connections=[];
async function connect(){
 const client=new Client({name:'datacore-runtime-qualification',version:'1'});
 const transport=new StdioClientTransport({command:process.execPath,args:['--require',path.join(cwd,'deny-archive.cjs'),path.join(cwd,'node_modules/@plur-ai/mcp/dist/index.js')],env:{PATH:process.env.PATH,HOME:root,PLUR_PATH:path.join(root,'store'),PLUR_DISABLE_EMBEDDINGS:'1',PLUR_TELEMETRY:'0'},stderr:'pipe'});
 connections.push(transport);let diagnostics='';transport.stderr?.on('data',d=>{diagnostics+=d.toString();if(diagnostics.length>1048576)transport.close()});
 await client.connect(transport);return{client,transport};
}
async function call(client,name,args={}){
 const r=await client.callTool({name,arguments:args});assert.notEqual(r.isError,true,JSON.stringify(r));return r;
}
try{
 const a=await connect();const tools=(await a.client.listTools()).tools;
 assert(tools.some(t=>t.name==='plur_status'));assert.equal(new Set(tools.map(t=>t.name)).size,tools.length);
 const statements=['Fixturecobalt keeps the violet calibration sample.','Fixturecobalt preserves the amber recovery sample.'];
 const b=await connect();
 await Promise.all(statements.map((statement,i)=>call((i?a:b).client,'plur_learn',{statement,scope:'global',domain:'software.fixture',tags:['fixturecobalt']})));
 const first=await call(a.client,'plur_status');
 await call(a.client,'plur_learn',{statement:statements[0],scope:'global',domain:'software.fixture',tags:['fixturecobalt']});
 const after=await call(a.client,'plur_status');
 const parse=r=>JSON.parse(r.content.filter(c=>c.type==='text').map(c=>c.text).join('\n'));
 const firstData=parse(first),afterData=parse(after);
 assert.equal(firstData.engram_count,2,JSON.stringify(firstData));assert.equal(afterData.engram_count,2,JSON.stringify(afterData));
 await a.transport.close();await b.transport.close();
 const restarted=await connect();const recalled=await call(restarted.client,'plur_recall',{query:'Fixturecobalt',mode:'keyword',scope:'global',limit:10});
 for(const s of statements)assert(JSON.stringify(recalled).includes(s),JSON.stringify(recalled));
 const invalid=await restarted.client.callTool({name:'plur_learn',arguments:{statement:3,scope:'global'}});assert.equal(invalid.isError,true);
 assert.equal(parse(await call(restarted.client,'plur_status')).engram_count,2);
 console.log('MCP initialize/list, two-process writes, exact retry, malformed input and restart retrieval PASS; archive loader unused');
 // Executable native inference, with a locally constructed Identity model.
 // Wire fields: https://github.com/onnx/onnx/blob/v1.20.1/onnx/onnx.proto
 const vi=n=>{const b=[];while(n>=128){b.push((n&127)|128);n>>=7}b.push(n);return Buffer.from(b)};
 const field=(n,x)=>{const b=typeof x==='string'?Buffer.from(x):x;return Buffer.concat([vi(n*8+2),vi(b.length),b])};
 const integer=(n,v)=>Buffer.concat([vi(n*8),vi(v)]);
 const info=n=>Buffer.concat([field(1,n),field(2,field(1,Buffer.concat([integer(1,1),field(2,field(1,integer(1,3)))])))]);
 const graph=Buffer.concat([field(1,Buffer.concat([field(1,'x'),field(2,'y'),field(4,'Identity')])),field(2,'fixture'),field(11,info('x')),field(12,info('y'))]);
 const model=Buffer.concat([integer(1,8),field(7,graph),field(8,integer(2,13))]);
 const ort=require('onnxruntime-node');const session=await ort.InferenceSession.create(model,{executionProviders:['cpu'],intraOpNumThreads:1,interOpNumThreads:1});
 try{const r=await session.run({x:new ort.Tensor('float32',new Float32Array([1.25,-2,3]),[3])});assert.deepEqual([...r.y.data],[1.25,-2,3]);}finally{await session.release()}
 await import('@huggingface/transformers');
 const sharp=require('sharp');const hv=sharp.versions.heif.split('.').map(Number);assert(hv[0]>1||(hv[0]===1&&(hv[1]>23||(hv[1]===23&&hv[2]>=2))));console.log('sharp native versions',JSON.stringify(sharp.versions));
 const png=await sharp({create:{width:2,height:2,channels:3,background:'#abcdef'}}).png().toBuffer();assert.equal((await sharp(png).metadata()).width,2);
 console.log('CPU ONNX inference, transformers import and sharp/libheif image roundtrip PASS');
}finally{await Promise.allSettled(connections.map(t=>t.close()));await rm(root,{recursive:true,force:true})}
