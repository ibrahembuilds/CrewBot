'use strict';
// Studio: brand images, videos, landing pages and funnels made by AI employees through OpenRouter.
let studioTab='image',studioFilter='all',studioComposer=null,gallerySignature='',catalogs={},catalogLoading={},catalogError={};
const purposeLabels={logo:'Logo',social_post:'Social post',story:'Story / Reel',poster:'Poster',banner:'Website banner',ad:'Ad creative',product_shot:'Product shot',illustration:'Illustration',other:'Custom'};
const roleSkills={creative:['Logos','Posters','Social graphics','Videos','Web pages'],marketing:['Campaigns','Social posts + visuals','Videos','Landing pages'],product:['Websites','Landing pages','Funnels','Code'],director:['Plans','Assigns work','Images'],growth:['Lead research','Market evidence'],offers:['Offers','Pricing','Sales scripts'],architect:['Architecture','Specs'],automation:['Workflows','Integrations'],success:['Onboarding','Handoffs']};
const pageBriefs={landing:['Landing page','Build a high-converting landing page for our main offer: hero with headline and subheadline, problem, solution, benefits, social proof section (placeholders clearly marked), FAQ and a strong call to action. Use our brand images if we have them; generate a hero image if you can.'],funnel:['Sales funnel','Build a 3-step sales funnel as separate pages in one funnel named "Main funnel": step 1 opt-in page with lead magnet, step 2 sales page for our core offer, step 3 thank-you page with next steps. Keep visual identity consistent across steps.'],website:['Business website','Build a business website home page with navigation, services, about, testimonials placeholders, contact section and footer, using our brand kit.'],webinar:['Webinar funnel','Build a 2-step webinar funnel named "Webinar": step 1 registration page, step 2 confirmation page with calendar reminder copy.']};
function assetUrl(a,download){return '/api/os/asset?company='+encodeURIComponent(companyId)+'&id='+a.id+(download?'&download=1':'');}
function money(v){return v==null?'cost not reported':'$'+Number(v).toFixed(Number(v)>0&&Number(v)<0.01?4:2);}
function studio(){return data.operating_system.studio;}
function skillsOf(e){return roleSkills[e.capability_role]||[];}
function skillChips(e){const row=n('div','skill-chips');skillsOf(e).forEach(s=>row.append(n('span','skill-chip',s)));return row;}
function builders(){return data.employees.filter(e=>['product','creative','marketing'].includes(e.capability_role));}
async function studioCall(name,body){const r=await fetch('/api/os/action',{method:'POST',headers:{'Content-Type':'application/json','X-Scout-Token':data.token},body:JSON.stringify({company_id:companyId,action:name,data:body})});const value=await r.json();if(!r.ok)throw Error(value.error||'Action failed');return value;}
function openrouterReady(){return data.operating_system.providers.find(p=>p.id==='openrouter')?.configured;}
async function loadCatalog(kind){if(catalogs[kind]||catalogLoading[kind]||!openrouterReady())return;catalogLoading[kind]=true;try{catalogs[kind]=(await studioCall('media_catalog',{kind})).models;catalogError[kind]=null;}catch(e){catalogError[kind]=e.message;}finally{catalogLoading[kind]=false;}if(studioComposer)studioComposer.refreshModels();}
function modelSelect(kind,current){const select=n('select');select.name='model';const fill=()=>{const list=catalogs[kind]||[],ids=new Set(list.map(m=>m.id)),keep=select.value||current;select.replaceChildren();const first=n('option','','Default — '+current);first.value=current;select.append(first);list.filter(m=>m.id!==current).forEach(m=>{const o=n('option','',m.name+' — '+m.id);o.value=m.id;select.append(o);});select.value=ids.has(keep)||keep===current?keep:current;};fill();select.fill=fill;return select;}
function labeled(text,input,help){const l=n('label','',text);l.append(input);if(help)l.append(n('p','form-help',help));return l;}
function options(select,values,labels){const keep=select.value;select.replaceChildren(...values.map(v=>{const o=n('option','',labels?.[v]||String(v));o.value=v;return o;}));if(values.map(String).includes(keep))select.value=keep;}
function imagePicker(name,label){const select=n('select');select.name=name;const fill=()=>{const keep=select.value;const none=n('option','','None');none.value='';select.replaceChildren(none,...studio().assets.filter(a=>a.kind==='image'&&a.mime!=='image/svg+xml').slice().reverse().map(a=>{const o=n('option','',a.title);o.value=a.id;return o;}));select.value=[...select.options].some(o=>o.value===keep)?keep:'';};fill();select.fill=fill;return labeled(label,select);}

function imageComposer(){
 const s=data.operating_system.settings,form=n('form','studio-form'),purposes=n('div','purpose-chips');purposes.setAttribute('role','radiogroup');purposes.setAttribute('aria-label','What are you making?');
 let purpose='logo';
 const aspect=n('select');aspect.name='aspect_ratio';options(aspect,studio().aspects);aspect.value=studio().purposes[purpose];
 Object.keys(purposeLabels).forEach(key=>{const b=btn(purposeLabels[key]+(studio().purposes[key]?' · '+studio().purposes[key]:''),'purpose-chip'+(key===purpose?' selected':''),()=>{purpose=key;all('.purpose-chip',purposes).forEach(c=>c.classList.toggle('selected',c===b));all('.purpose-chip',purposes).forEach(c=>c.setAttribute('aria-checked',String(c===b)));aspect.value=studio().purposes[key];});b.setAttribute('role','radio');b.setAttribute('aria-checked',String(key===purpose));purposes.append(b);});
 const prompt=n('textarea');prompt.name='prompt';prompt.rows=4;prompt.required=true;prompt.maxLength=4000;prompt.placeholder='Describe it like an art director: subject, style, composition, colors, text to include, mood, what to avoid…';
 const model=modelSelect('image',s.image_model),count=n('select');count.name='n';options(count,[1,2,3,4]);
 const quality=n('select');quality.name='quality';options(quality,['','low','medium','high'],{'':'Provider default'});
 const background=n('select');background.name='background';options(background,['','transparent','opaque'],{'':'Provider default'});
 const brand=n('input');brand.type='checkbox';brand.name='use_brand';brand.checked=true;const brandLabel=n('label','check-label','Apply brand kit (name, audience, colors, visual style)');brandLabel.prepend(brand);
 const reference=imagePicker('reference','Reference image (optional)');
 const qualityLabel=labeled('Quality',quality),backgroundLabel=labeled('Background',background),note=n('p','form-help');
 const sync=()=>{const info=(catalogs.image||[]).find(m=>m.id===model.value);qualityLabel.hidden=!!info&&!info.parameters.includes('quality');backgroundLabel.hidden=!!info&&!info.parameters.includes('background');const supported=info?.aspect_ratios;all('option',aspect).forEach(o=>o.disabled=!!supported&&o.value!=='auto'&&!supported.includes(o.value));note.textContent=info?(info.vector?'Vector model: logos come back as SVG. ':'')+(supported?'Supported ratios: '+supported.join(', '):''):catalogError.image||(openrouterReady()?'Loading the live OpenRouter image model list…':'Add your OpenRouter key in Settings to browse every image model.');};
 model.addEventListener('change',sync);
 const grid=n('div','studio-grid');grid.append(labeled('Image model',model),labeled('Aspect ratio',aspect),labeled('How many',count),qualityLabel,backgroundLabel,reference);
 const submit=n('button','primary','Generate image ↗');submit.type='submit';
 form.append(n('h3','','What are you making?'),purposes,labeled('Art direction',prompt),grid,note,brandLabel,submit);
 form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;submit.textContent='Generating… this can take up to a minute';try{const body={prompt:prompt.value,purpose,model:model.value,aspect_ratio:aspect.value,n:Number(count.value),use_brand:brand.checked};if(!qualityLabel.hidden&&quality.value)body.quality=quality.value;if(!backgroundLabel.hidden&&background.value)body.background=background.value;const ref=q('select',reference).value;if(ref)body.reference_asset_ids=[ref];const r=await studioCall('generate_image',body);tell(r.assets.length+' image(s) saved · '+money(r.cost));studioFilter='image';await refresh();}catch(err){tell(err.message);}finally{submit.disabled=false;submit.textContent='Generate image ↗';}});
 return {node:form,refreshModels(){model.fill();sync();},refreshAssets(){q('select',reference).fill();},sync};
}
function videoComposer(){
 const s=data.operating_system.settings,form=n('form','studio-form');
 const prompt=n('textarea');prompt.rows=4;prompt.required=true;prompt.maxLength=4000;prompt.placeholder='Shot description: subject, action, camera movement, lighting, style. Example: slow push-in on our product on a marble counter, morning light, shallow depth of field.';
 const model=modelSelect('video',s.video_model),duration=n('select'),aspect=n('select'),resolution=n('select');
 const audio=n('input');audio.type='checkbox';audio.checked=true;const audioLabel=n('label','check-label','Generate audio when the model supports it');audioLabel.prepend(audio);
 const brand=n('input');brand.type='checkbox';brand.checked=true;const brandLabel=n('label','check-label','Apply brand kit');brandLabel.prepend(brand);
 const frame=imagePicker('first_frame','Start from image (optional, image-to-video)'),pricing=n('p','form-help');
 const sync=()=>{const info=(catalogs.video||[]).find(m=>m.id===model.value);options(duration,['',...(info?.durations||[])],{'':'Model default'});options(aspect,['',...(info?.aspect_ratios||['16:9','9:16','1:1'])],{'':'Model default'});options(resolution,['',...(info?.resolutions||[])],{'':'Model default'});const skus=Object.entries(info?.pricing||{});pricing.textContent=info?(skus.length?'OpenRouter pricing: '+skus.slice(0,4).map(([k,v])=>k.replace(/_/g,' ')+' $'+v).join(' · '):'Pricing listed on OpenRouter.')+' Cost is charged only for completed videos.':catalogError.video||(openrouterReady()?'Loading the live OpenRouter video model list…':'Add your OpenRouter key in Settings to browse every video model.');};
 model.addEventListener('change',sync);sync();
 const grid=n('div','studio-grid');grid.append(labeled('Video model',model),labeled('Duration (seconds)',duration),labeled('Aspect ratio',aspect),labeled('Resolution',resolution),frame);
 const submit=n('button','primary','Start video render ↗');submit.type='submit';
 form.append(n('h3','','Short brand video'),labeled('Shot description',prompt),grid,pricing,audioLabel,brandLabel,submit);
 form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{const body={prompt:prompt.value,model:model.value,generate_audio:audio.checked,use_brand:brand.checked};if(duration.value)body.duration=Number(duration.value);if(aspect.value)body.aspect_ratio=aspect.value;if(resolution.value)body.resolution=resolution.value;const first=q('select',frame).value;if(first)body.first_frame_asset_id=first;await studioCall('generate_video',body);tell('Video render started. It appears in the gallery when ready (usually under a few minutes).');studioFilter='jobs';await refresh();}catch(err){tell(err.message);}finally{submit.disabled=false;}});
 return {node:form,refreshModels(){model.fill();sync();},refreshAssets(){q('select',frame).fill();},sync};
}
function pageComposer(){
 const form=n('form','studio-form'),team=builders();
 form.append(n('h3','','Landing pages, websites & funnels'),n('p','form-help','Your software engineer, designer or marketer writes complete HTML pages with your brand images. Pages preview in an isolated sandbox and export as single files.'));
 if(!team.length){const blank=empty('Hire a builder first','Nova (software engineer) builds websites, landing pages and funnels.');blank.append(btn('Hire Nova · software engineer','primary',()=>hire('engineer')));form.append(blank);return {node:form,refreshModels(){},refreshAssets(){}};}
 const owner=n('select');options(owner,team.map(e=>e.id),Object.fromEntries(team.map(e=>[e.id,e.name+' · '+e.role])));const preferred=team.find(e=>e.capability_role==='product');if(preferred)owner.value=preferred.id;
 const kind=n('select');options(kind,Object.keys(pageBriefs),Object.fromEntries(Object.entries(pageBriefs).map(([k,v])=>[k,v[0]])));
 const brief=n('textarea');brief.rows=5;brief.required=true;brief.maxLength=20000;brief.value=pageBriefs.landing[1];
 kind.addEventListener('change',()=>brief.value=pageBriefs[kind.value][1]);
 const submit=n('button','primary','Send brief ↗');submit.type='submit';
 form.append(labeled('Builder',owner),labeled('What to build',kind),labeled('Brief',brief,'Add offer details, sections, tone or a reference site. The builder saves each page with build_web_page.'),submit);
 form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;const r=await action('chat',{employee_id:owner.value,message:brief.value});submit.disabled=false;if(r){tell('Brief sent. Follow progress in the conversation; pages appear here when saved.');chatWith(owner.value);conversationId=r.conversation_id;paintChat();}});
 return {node:form,refreshModels(){},refreshAssets(){}};
}
function buildComposer(){const make={image:imageComposer,video:videoComposer,page:pageComposer}[studioTab];studioComposer={...make(),company:companyId,tab:studioTab,builders:builders().length};const host=q('#studio-composer');host.replaceChildren(studioComposer.node);studioComposer.sync?.();loadCatalog(studioTab==='video'?'video':'image');}
function paintSpend(){const sp=studio().spend,root=q('#studio-spend'),pct=Math.min(100,sp.budget?sp.total/sp.budget*100:0);root.replaceChildren();const bar=n('div','spend-bar');const fill=n('span');fill.style.width=pct+'%';if(pct>=80)fill.classList.add('warn');bar.append(fill);bar.setAttribute('role','meter');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax',String(sp.budget));bar.setAttribute('aria-valuenow',String(sp.total));bar.setAttribute('aria-label','Monthly AI spend');root.append(n('strong','',money(sp.total)+' of $'+Number(sp.budget).toFixed(2)+' this month'),bar,n('small','','OpenRouter-reported: chat '+money(sp.by_kind.chat||0)+' · images '+money(sp.by_kind.image||0)+' · video '+money(sp.by_kind.video||0)+'. Failed generations are not billed.'));}
function latestPages(pages){const latest=new Map();pages.forEach(p=>{const key=p.funnel+'\u0000'+p.step+'\u0000'+p.title;if(!latest.has(key)||latest.get(key).version<p.version)latest.set(key,p);});return [...latest.values()];}
function assetCard(a){
 const card=n('article','asset-card '+a.kind),preview=n('div','asset-preview');
 if(a.kind==='image'){const img=n('img');img.src=assetUrl(a);img.alt=a.title;img.loading='lazy';preview.append(img);preview.classList.add('ratio-'+(a.aspect_ratio||'1:1').replace(':','-'));}
 else if(a.kind==='video'){const v=n('video');v.src=assetUrl(a);v.controls=true;v.preload='metadata';v.setAttribute('aria-label',a.title);preview.append(v);}
 else{const tile=n('button','page-tile');tile.type='button';tile.append(n('span','page-kind',a.purpose),n('strong','',a.title),n('small','',a.funnel?'Funnel “'+a.funnel+'” · step '+a.step:'Single page'));tile.addEventListener('click',()=>previewPage(a));preview.append(tile);}
 const who=a.employee_id&&person(a.employee_id)?person(a.employee_id).name:'You';
 card.append(preview,n('h3','',a.title),n('p','asset-meta',[purposeLabels[a.purpose]||a.purpose,a.model,who,a.kind==='page'?'v'+a.version:money(a.cost)].filter(Boolean).join(' · ')));
 const actions=n('div','card-actions');const dl=n('a','',a.kind==='page'?'Download HTML ↓':'Download ↓');dl.href=assetUrl(a,true);actions.append(dl);
 if(a.kind==='page')actions.append(btn('Preview','primary',()=>previewPage(a)));
 if(a.kind==='image'&&a.mime!=='image/svg+xml'){actions.append(btn('Animate','quiet',()=>{studioTab='video';buildComposer();q('select[name=first_frame]').value=a.id;paintStudioTabs();}));if(a.purpose==='logo')actions.append(btn('Use as workspace logo','quiet',async()=>{if(await action('branding',{logo_asset_id:a.id}))tell('Workspace logo updated.');}));}
 if(a.prompt&&a.kind!=='page')actions.append(btn('Prompt','quiet',()=>{const c=n('div');c.append(n('p','task-result',a.prompt),n('p','form-help','Model: '+(a.model||'—')+' · Brand kit '+(a.brand_applied?'applied':'not applied')+' · '+money(a.cost)));dialog(a.title,c);}));
 actions.append(btn('Delete','quiet danger',async()=>{if(confirm('Delete “'+a.title+'”? This removes the file.'))await action('delete_asset',{id:a.id});}));
 card.append(actions);return card;
}
function previewPage(a){const content=n('div','page-preview'),bar=n('div','preview-bar'),frame=n('iframe');frame.src=assetUrl(a);frame.title='Preview of '+a.title;frame.setAttribute('sandbox','allow-scripts allow-forms allow-popups allow-modals');bar.append(btn('Desktop','secondary',()=>frame.style.width='100%'),btn('Mobile','secondary',()=>frame.style.width='390px'));const open=n('a','','Open in new tab ↗');open.href=assetUrl(a);open.target='_blank';open.rel='noopener';const dl=n('a','','Download HTML ↓');dl.href=assetUrl(a,true);bar.append(open,dl);content.append(bar,frame,n('p','form-help','Sandboxed preview: the page cannot reach your workspace or the network. Forms are previews until you connect a backend.'));dialog(a.title,content);q('#os-dialog').classList.add('wide');q('#os-dialog').addEventListener('close',()=>q('#os-dialog').classList.remove('wide'),{once:true});}
function paintGallery(force){
 const root=q('#studio-gallery'),st=studio();
 // Rebuild only on real changes so playing videos and loaded images survive the 5 s refresh.
 const signature=[companyId,studioFilter,st.assets.map(a=>a.id+(a.employee_id||'')).join(','),st.jobs.map(j=>j.id+j.status).join(','),data.employees.map(e=>e.id+e.name).join(',')].join('|');
 if(!force&&signature===gallerySignature&&root.childElementCount)return;gallerySignature=signature;root.replaceChildren();
 const jobs=st.jobs.filter(j=>j.status!=='completed').slice().reverse();
 if(studioFilter==='jobs'||studioFilter==='all'){jobs.forEach(j=>{const c=n('article','asset-card job');const head=n('div','section-top');head.append(n('h3','',j.title),tag(j.status==='failed'?'blocked':'running'));c.append(head,n('p','asset-meta',j.model+' · '+(j.status==='failed'?'Failed: '+(j.error||'unknown error'):({pending:'Queued at provider',in_progress:'Rendering',downloading:'Downloading'}[j.status]||j.status))));root.append(c);});}
 const assets=st.assets.filter(a=>studioFilter==='all'||a.kind===studioFilter);
 const pages=latestPages(assets.filter(a=>a.kind==='page')),funnels={};pages.filter(p=>p.funnel).forEach(p=>(funnels[p.funnel]=funnels[p.funnel]||[]).push(p));
 Object.entries(funnels).forEach(([name,steps])=>{const group=n('section','funnel-group');group.append(n('span','eyebrow','FUNNEL'),n('h3','',name));const track=n('div','funnel-track');steps.sort((x,y)=>x.step-y.step).forEach(p=>track.append(assetCard(p)));group.append(track);root.append(group);});
 [...assets.filter(a=>a.kind!=='page'),...pages.filter(p=>!p.funnel)].sort((x,y)=>y.created_at.localeCompare(x.created_at)).forEach(a=>root.append(assetCard(a)));
 if(!root.childElementCount)root.append(empty(studioFilter==='jobs'?'No renders in progress':'Nothing here yet','Generate a logo, social post, poster or video above, or brief your engineer to build a landing page or funnel.'));
}
function paintStudioTabs(){all('#studio-tabs button').forEach(b=>{b.classList.toggle('selected',b.dataset.tab===studioTab);b.setAttribute('aria-selected',String(b.dataset.tab===studioTab));});all('#studio-filters button').forEach(b=>b.classList.toggle('selected',b.dataset.filter===studioFilter));}
function paintStudio(){
 if(!q('#studio-composer'))return;
 const stale=!studioComposer||studioComposer.company!==companyId||studioComposer.tab!==studioTab||(studioTab==='page'&&studioComposer.builders!==builders().length);
 if(stale)buildComposer();else if(!q('#studio-composer').contains(document.activeElement))studioComposer.refreshAssets();
 const ex=q('#studio-export');ex.href='/api/os/asset-export?company='+encodeURIComponent(companyId);ex.hidden=!studio().assets.length;
 paintSpend();paintGallery();paintStudioTabs();
}
async function hire(id){const r=await action('hire_expert',{expert:id});if(r)tell(r.name+' joined your crew as '+r.role+'.');return r;}
function paintRoster(){
 let section=q('#expert-roster');if(!section){section=n('section','expert-roster');section.id='expert-roster';q('#team-view').append(section);}
 section.replaceChildren();const head=n('div','section-top'),copy=n('div');copy.append(n('span','eyebrow','READY-TO-HIRE EXPERTS'),n('h2','','Hire an expert in one click'),n('p','form-help','Each expert comes with a role, operating instructions and only the tools that role needs. Edit any of it after hiring.'));head.append(copy);section.append(head);
 const grid=n('div','employees-grid');
 data.operating_system.experts.forEach(x=>{const hired=data.employees.filter(e=>e.capability_role===x.capability_role&&e.id.startsWith(x.id)).length,card=n('article','employee-card expert-card'),top=n('div','card-head'),c=n('div');c.append(n('h3','',x.name),n('small','',x.role));top.append(icon({id:x.id,name:x.name,capability_role:x.capability_role}),c);card.append(top,skillChips(x));const actions=n('div','card-actions');actions.append(btn(hired?'Hire another':'Hire '+x.name,hired?'secondary':'primary',()=>hire(x.id)));if(hired)actions.append(n('span','tag done',hired+' on your crew'));card.append(actions);grid.append(card);});
 section.append(grid);
}
function taskAssets(t){const ids=t.asset_ids||[],wrap=n('div');if(t.cost)wrap.append(n('p','review-meta','Provider-reported cost for this task: '+money(t.cost)));const items=studio().assets.filter(a=>ids.includes(a.id));if(items.length){wrap.append(n('h3','','Created in Studio'));const grid=n('div','task-assets');items.forEach(a=>grid.append(assetCard(a)));wrap.append(grid);}return wrap;}
q('#studio-tabs').addEventListener('click',e=>{const b=e.target.closest('button[data-tab]');if(!b)return;studioTab=b.dataset.tab;buildComposer();paintStudioTabs();});
q('#studio-filters').addEventListener('click',e=>{const b=e.target.closest('button[data-filter]');if(!b)return;studioFilter=b.dataset.filter;paintGallery();paintStudioTabs();});
