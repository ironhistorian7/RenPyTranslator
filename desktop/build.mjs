import {build} from 'esbuild';
import {mkdir,copyFile} from 'node:fs/promises';
await mkdir('dist',{recursive:true});
await build({entryPoints:['src/main.ts','src/preload.ts'],outdir:'dist',bundle:true,platform:'node',format:'cjs',external:['electron'],target:'node24'});
await build({entryPoints:['src/renderer.ts'],outfile:'dist/renderer.js',bundle:true,platform:'browser',target:'chrome140'});
for(const file of ['index.html','style.css']) await copyFile('src/'+file,'dist/'+file);
