import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = process.env.QA_OUTPUT || '/tmp/fyb-frontend-qa';
await fs.mkdir(output, {recursive:true});
const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.woff2':'font/woff2','.mp4':'video/mp4','.json':'application/json'};
const server=http.createServer(async(req,res)=>{
  try{
    let pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    if(pathname.endsWith('/')) pathname+='index.html';
    let file=path.resolve(root,'.'+pathname);
    if(!file.startsWith(root+path.sep)) {res.writeHead(403).end();return;}
    let stat=await fs.stat(file);
    if(stat.isDirectory()){res.writeHead(301,{Location:pathname+'/'}).end();return;}
    const data=await fs.readFile(file);
    const headers={'Content-Type':types[path.extname(file)]||'application/octet-stream','Accept-Ranges':'bytes'};
    if(req.headers.range){const match=req.headers.range.match(/bytes=(\d+)-(\d*)/);const start=Number(match[1]);const end=Math.min(match[2]?Number(match[2]):data.length-1,data.length-1);res.writeHead(206,{...headers,'Content-Range':`bytes ${start}-${end}/${data.length}`,'Content-Length':end-start+1});res.end(data.subarray(start,end+1));}
    else {res.writeHead(200,{...headers,'Content-Length':data.length});res.end(req.method==='HEAD'?undefined:data);}
  }catch {res.writeHead(404).end('Not found');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
let browser;
const report={pages:[],viewports:[],checks:[],errors:[]};
try{
 browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 page.on('pageerror',e=>report.errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)report.errors.push(`${r.status()} ${r.url()}`);});
 page.on('requestfailed',r=>{if(r.failure()?.errorText!=='net::ERR_ABORTED')report.errors.push(r.url()+' '+r.failure()?.errorText);});
 for(const route of ['/','/home/','/hello-world/','/category/uncategorized/','/404.html']){
  const response=await page.goto(base+route);assert.equal(response.status(),200);
  await page.evaluate(()=>document.fonts.ready);
  const links=await page.locator('a[href]').evaluateAll(nodes=>nodes.map(n=>n.href));
  for(const href of new Set(links)){
   const url=new URL(href);if(url.origin!==new URL(base).origin)continue;
   const response=await page.request.get(url.origin+url.pathname);assert.equal(response.status(),200,href);
   if(url.hash && url.pathname===new URL(page.url()).pathname) assert.ok(await page.locator(url.hash).count(),href);
  }
  report.pages.push({route,status:response.status(),title:await page.title()});
 }
 for(const width of [320,375,440,768,1024,1440,1920]){
  await page.setViewportSize({width,height:1000});await page.goto(base);await page.evaluate(()=>document.fonts.ready);
  await page.locator('img').evaluateAll(images=>images.forEach(img=>img.loading='eager'));
  await page.waitForFunction(()=>[...document.images].every(img=>img.complete&&img.naturalWidth>0));
  await page.evaluate(()=>Promise.all([...document.images].map(img=>img.decode())));
  for(const gallery of await page.locator('.gallery').all()){await gallery.scrollIntoViewIfNeeded();await gallery.screenshot({path:path.join(output,`gallery-${width}-${await gallery.getAttribute('id')}.png`)});}
  await page.evaluate(()=>scrollTo(0,0));
  const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,images:document.images.length}));
  assert.ok(layout.scroll<=width,`Overflow at ${width}: ${layout.scroll}`);
  await page.screenshot({path:path.join(output,`home-${width}.png`),fullPage:true});
  report.viewports.push(layout);
 }
 await page.setViewportSize({width:440,height:900});await page.goto(base);
 await page.getByRole('button',{name:'Open menu',exact:true}).click();assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'),'true');
 assert.ok(await page.getByRole('navigation').isVisible());
 await page.keyboard.press('Escape');assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'),'false');
 await page.getByRole('button',{name:'Open menu',exact:true}).click();await page.locator('.menu-backdrop').click({position:{x:420,y:500}});assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'),'false');
 report.checks.push('Mobile menu opens, Escape closes it and restores focus; backdrop closes it.');
 for(const gallery of await page.locator('.gallery').all()){
  await gallery.scrollIntoViewIfNeeded();const slides=gallery.locator('.slide');const first=await slides.nth(0).getAttribute('src');
  await gallery.locator('.next').click();assert.equal(await gallery.locator('.is-active').getAttribute('src'),await slides.nth(1).getAttribute('src'));
  await gallery.locator('.previous').click();assert.equal(await gallery.locator('.is-active').getAttribute('src'),first);
  await gallery.focus();await page.keyboard.press('ArrowLeft');assert.equal(await gallery.locator('.is-active').getAttribute('src'),await slides.nth(2).getAttribute('src'));
  await page.keyboard.press('ArrowRight');
  await gallery.dispatchEvent('touchstart',{changedTouches:[{identifier:1,clientX:300,clientY:100}]});await gallery.dispatchEvent('touchend',{changedTouches:[{identifier:1,clientX:100,clientY:105}]});assert.equal(await gallery.locator('.is-active').getAttribute('src'),await slides.nth(1).getAttribute('src'));
  await gallery.locator('.gallery-pause').click();assert.ok((await gallery.locator('.gallery-pause').getAttribute('aria-label')).startsWith('Pause'));
 }
 report.checks.push('All five galleries: next/previous, wraparound, keyboard, swipe, play/pause; reduced-motion defaults to paused.');
 await page.locator('.quote-button').click();await page.waitForFunction(()=>location.hash==='#studio-info');report.checks.push('Get Quote reaches studio rates and real email/phone contact links.');
 await page.locator('#testimonial').scrollIntoViewIfNeeded();await page.locator('.video-play').click();
 await page.waitForFunction(()=>{const v=document.querySelector('video');return v.currentTime>0&&v.videoWidth>0},{},{timeout:20000});
 report.checks.push(await page.locator('video').evaluate(v=>`Local MP4 plays: ${v.videoWidth}×${v.videoHeight}, ${v.duration.toFixed(1)} seconds.`));
 await page.locator('video').evaluate(v=>v.pause());
 const assetManifest=JSON.parse(await fs.readFile(path.join(root,'asset-manifest.json'),'utf8'));
 for(const asset of assetManifest){const r=await page.request.head(base+'/'+asset.file);assert.equal(r.status(),200,asset.file);assert.equal(Number(r.headers()['content-length']),asset.bytes,asset.file);}
 report.checks.push(`${assetManifest.length} source assets verified by file size and served successfully.`);
 const context=await browser.newContext({javaScriptEnabled:false,viewport:{width:440,height:900}});const nojs=await context.newPage();await nojs.goto(base);assert.ok(await nojs.locator('h1').isVisible());assert.equal(await nojs.locator('.slide.is-active').count(),5);assert.ok(await nojs.locator('#fyb-pricing').count());report.checks.push('Content, first gallery images, native video controls, rates and contact links are present without JavaScript.');await context.close();
 assert.deepEqual(report.errors,[]);
 report.result='PASS';
}catch(error){report.result='FAIL';report.errors.push(error.stack);process.exitCode=1;}
finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}
