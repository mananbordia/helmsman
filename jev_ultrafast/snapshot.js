(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const onScreen = e => {
    if (!visible(e)) return false;
    const r=e.getBoundingClientRect();
    return r.width>=20 && r.height>=20 && r.bottom>0 && r.right>0 &&
      r.top<innerHeight && r.left<innerWidth;
  };
  // Invisible mode (reCAPTCHA v3) renders an anchor frame that nobody interacts with: it
  // scores the session and passes. Treating it as a challenge stopped a run on a page
  // whose only real blocker was a sign-in, and the frame was already gone by the next
  // look. A bframe is the actual challenge popup, so it still counts.
  const invisibleAnchor=frame=>{
    try {
      const url=new URL(frame.src,location.href);
      return url.pathname.endsWith('/recaptcha/api2/anchor') && url.searchParams.get('size')==='invisible';
    } catch { return false; }
  };
  cache.detectCaptcha=()=>{
    const challenge=document.querySelector('form#challenge-form');
    const challengeStage=document.querySelector('#challenge-stage');
    if (challenge?.action?.includes('/cdn-cgi/challenge-platform/') ||
        ((challenge || challengeStage) && /just a moment|verify you are human/i.test(document.title))) {
      return {provider:'cloudflare',surface:'challenge_page'};
    }
    for (const frame of document.querySelectorAll('iframe')) {
      if (!onScreen(frame) || invisibleAnchor(frame)) continue;
      let host='', path='';
      try { const url=new URL(frame.src,location.href); host=url.hostname; path=url.pathname; } catch {}
      const title=frame.title.toLowerCase();
      if (((host==='www.google.com' || host==='www.recaptcha.net' || host==='recaptcha.net') &&
          path.startsWith('/recaptcha/')) || title.includes('recaptcha')) {
        return {provider:'recaptcha',surface:'widget'};
      }
      if (host==='hcaptcha.com' || host.endsWith('.hcaptcha.com') || title.includes('hcaptcha')) {
        return {provider:'hcaptcha',surface:'widget'};
      }
      if (host==='challenges.cloudflare.com' ||
          (title.includes('cloudflare') && title.includes('challenge'))) {
        return {provider:'turnstile',surface:'widget'};
      }
      if (host==='arkoselabs.com' || host.endsWith('.arkoselabs.com') || title.includes('arkose')) {
        return {provider:'arkose',surface:'widget'};
      }
      if (/captcha|verify you are human/i.test(title)) {
        return {provider:'unknown',surface:'widget'};
      }
    }
    for (const [selector,provider] of [
      ['.g-recaptcha','recaptcha'],['.h-captcha','hcaptcha'],['.cf-turnstile','turnstile'],
      ['.geetest_captcha','geetest'],['.frc-captcha','friendlycaptcha']
    ]) {
      // A widget declared invisible asks nothing of a person, so it is not a challenge.
      if ([...document.querySelectorAll(selector)].some(
        e => onScreen(e) && e.getAttribute('data-size') !== 'invisible')) {
        return {provider,surface:'widget'};
      }
    }
    const humanPrompt=/(?:confirm|verify|prove).{0,100}(?:human|not a robot)/i;
    const challengePrompt=/(?:complete.{0,80}challenge|select all (?:squares|images|tiles)|captcha)/i;
    for (const e of document.querySelectorAll('div,[role="dialog"],section')) {
      if (!onScreen(e)) continue;
      const copy=e.innerText?.slice(0,1500)||'';
      if (!humanPrompt.test(copy) || !challengePrompt.test(copy)) continue;
      if (e.getAttribute('role')==='dialog' || e.getAttribute('aria-modal')==='true' ||
          ['fixed','absolute'].includes(getComputedStyle(e).position)) {
        return {provider:'unknown',surface:'challenge_page'};
      }
    }
    return null;
  };
  // A challenge is read like any other page, because its text is evidence and blanking it
  // is what left a stop with nothing to explain itself. Only the *actions* are withheld,
  // and only when the challenge is a wall: the page text is the challenge, or the page
  // offers nothing else to do. A provider widget beside real controls is not a wall, and
  // stopping on one is what hid a sign-in behind a false captcha.
  const captcha=cache.detectCaptcha();
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly]),
    cache.detectCaptcha()];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      const searchForm=e.closest('form[role="search"]');
      const canSubmitSearch=editable && searchForm?.method==='get' && value.trim();
      if (canSubmitSearch) actions.push({...base,kind:'submit',value,label:'Submit search with Enter'});
      else if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  // A text node keeps a rect even when an ancestor has clipped it away or its own
  // colour is transparent, so both are checked separately. Without them a visually
  // hidden helper reads as ordinary page content. Overflow is memoised because
  // siblings share ancestors and getComputedStyle is the expensive half of this.
  const overflow=new WeakMap(), faded=new WeakMap();
  const clips=a=>{
    if (!overflow.has(a)) {
      const s=getComputedStyle(a);
      overflow.set(a,/hidden|clip/.test(s.overflow+s.overflowX+s.overflowY)||s.clip!=='auto');
    }
    return overflow.get(a);
  };
  const painted=(e,r)=>{
    if (!faded.has(e)) faded.set(e,getComputedStyle(e).color==='rgba(0, 0, 0, 0)');
    if (faded.get(e)) return false;
    // How much of the line survives every clipping ancestor, as a fraction of the line
    // itself. A one-pixel visually-hidden helper shows a fraction of a percent of the
    // text it holds, while truncated text still shows full lines and normal layout
    // clips nothing, so a ratio separates them where containment tests did not: a
    // tolerance rejected visible text over a single pixel of sub-pixel rounding, and a
    // relative height test still dropped real results from a live page.
    const area=r.width*r.height;
    let visible=area;
    for (let a=e; a && a!==document.documentElement; a=a.parentElement) {
      if (!clips(a)) continue;
      const box=a.getBoundingClientRect();
      const w=Math.min(box.right,r.right)-Math.max(box.left,r.left);
      const h=Math.min(box.bottom,r.bottom)-Math.max(box.top,r.top);
      if (w<=0||h<=0) return false;
      visible=Math.min(visible,w*h);
    }
    return visible>area*0.1;
  };
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (!(r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth)) continue;
    if (!painted(parent,r)) continue;
    words.push(value); length+=value.length;
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],captcha];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  // Scroll and wait are always offered, so they say nothing about whether the page is
  // usable. A wall is a challenge with no other real control on it.
  const ordinary=actions.filter(a=>a.kind!=='scroll'&&a.kind!=='wait');
  const challengeWall=!!captcha && (captcha.surface!=='widget' || !ordinary.length);
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions:challengeWall?[]:actions,marker,page_key,guards,
    omitted_actions,captcha};
})()
