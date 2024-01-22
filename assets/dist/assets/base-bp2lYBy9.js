class a{constructor(t,e={}){this.options=Object.assign({closeClickOutside:!1,forceStateClose:!1,forceStateOpen:!1,closeEsc:!1,forceStateRestore:!0},e),this.detail=t,this.summary=t.querySelector(":scope > summary"),this._previousStates={}}getMatchMedia(t,e){if(t){if(e&&e===!0)return{matches:!0};if(e&&"matchMedia"in window)return window.matchMedia(e)}}init(){let t=this.getMatchMedia(this.detail,this.options.forceStateOpen),e=this.getMatchMedia(this.detail,this.options.forceStateClose);t&&t.matches&&e&&e.matches?this.setState(!this.detail.open):(t&&t.matches&&this.setState(!0),e&&e.matches&&this.setState(!1)),this.addListener(t,"for-open"),this.addListener(e,"for-close")}addListener(t,e){!t||!("addListener"in t)||t.addListener(s=>{s.matches?(this._previousStates[e]=this.detail.open,this.detail.open!==(e==="for-open")&&this.setState(e==="for-open")):this.options.forceStateRestore&&this._previousStates[e]!==void 0&&this.detail.open!==this._previousStates[e]&&this.setState(this._previousStates[e])})}toggle(){let t=new MouseEvent("click",{view:window,bubbles:!0,cancelable:!0});this.summary.dispatchEvent(t)}triggerClickToClose(){this.summary&&this.options.closeClickOutside&&this.toggle()}setState(t){t?this.detail.setAttribute("open","open"):this.detail.removeAttribute("open")}}class h{constructor(t){this.duration={open:200,close:150},this.detail=t,this.summary=this.detail.querySelector(":scope > summary");let e=this.detail.getAttribute("data-du-animate-target");if(e&&(this.content=this.detail.closest(e)),this.content||(this.content=this.summary.nextElementSibling),!this.content)throw new Error("For now <details-utils> requires a child element for animation.");this.summary.addEventListener("click",this.onclick.bind(this))}parseAnimationFrames(t,...e){let s=[];for(let o of e){let n={};n[t]=o,s.push(n)}return s}getKeyframes(t){let e=this.parseAnimationFrames("maxHeight","0px",`${this.getContentHeight()}px`);return t?e:e.filter(()=>!0).reverse()}getContentHeight(){if(this.contentHeight)return this.contentHeight;if(this.detail.open)return this.contentHeight=this.content.offsetHeight,this.contentHeight}animate(t,e){this.isPending=!0;let s=this.getKeyframes(t);this.animation=this.content.animate(s,{duration:e,easing:"ease-out"}),this.detail.classList.add("details-animating"),this.animation.finished.catch(o=>{}).finally(()=>{this.isPending=!1,this.detail.classList.remove("details-animating")}),t||this.animation.finished.catch(o=>{}).finally(()=>{this.detail.removeAttribute("open")})}open(){this.contentHeight?this.animate(!0,this.duration.open):requestAnimationFrame(()=>this.animate(!0,this.duration.open))}close(){this.animate(!1,this.duration.close)}useAnimation(){return"matchMedia"in window&&!window.matchMedia("(prefers-reduced-motion: reduce)").matches}onclick(t){t.target.closest("a[href]")||!this.useAnimation()||(this.isPending?this.animation&&this.animation.cancel():this.detail.open?(t.preventDefault(),this.close()):this.open())}}class u extends HTMLElement{constructor(){super(),this.attrs={animate:"animate",closeEsc:"close-esc",closeClickOutside:"close-click-outside",forceStateClose:"force-close",forceStateOpen:"force-open",forceStateRestore:"force-restore",toggleDocumentClass:"toggle-document-class",closeClickOutsideButton:"data-du-close-click"},this.options={},this._connect()}getAttributeValue(t){let e=this.getAttribute(t);return e===void 0||e===""?!0:e||!1}connectedCallback(){this._connect()}_connect(){if(this.children.length){this._init();return}this._observer=new MutationObserver(this._init.bind(this)),this._observer.observe(this,{childList:!0})}_init(){if(this.initialized)return;this.initialized=!0,this.options.closeClickOutside=this.getAttributeValue(this.attrs.closeClickOutside),this.options.closeEsc=this.getAttributeValue(this.attrs.closeEsc),this.options.forceStateClose=this.getAttributeValue(this.attrs.forceStateClose),this.options.forceStateOpen=this.getAttributeValue(this.attrs.forceStateOpen),this.options.forceStateRestore=this.getAttributeValue(this.attrs.forceStateRestore);let t=Array.from(this.querySelectorAll(":scope details"));for(let e of t)new a(e,this.options).init(),this.hasAttribute(this.attrs.animate)&&new h(e);this.bindCloseOnEsc(t),this.bindClickoutToClose(t),this.toggleDocumentClassName=this.getAttribute(this.attrs.toggleDocumentClass),this.toggleDocumentClassName&&this.bindToggleDocumentClass(t)}bindCloseOnEsc(t){this.options.closeEsc&&document.documentElement.addEventListener("keydown",e=>{if(e.keyCode===27){for(let s of t)if(s.open){let o=new a(s,this.options),n=o.getMatchMedia(s,this.options.closeEsc);(!n||n&&n.matches)&&o.toggle()}}},!1)}isChildOfParent(t,e){for(;t&&t.parentNode;){if(t.parentNode===e)return!0;t=t.parentNode}return!1}onClickoutToClose(t,e){let s=new a(t,this.options),o=s.getMatchMedia(t,this.options.closeClickOutside);if(o&&!o.matches)return;(e.target.hasAttribute(this.attrs.closeClickOutsideButton)||!this.isChildOfParent(e.target,t))&&t.open&&s.triggerClickToClose(t)}bindClickoutToClose(t){document.documentElement.addEventListener("mousedown",e=>{for(let s of t)this.onClickoutToClose(s,e)},!1),this.addEventListener("keypress",e=>{if(e.which===13||e.which===32)for(let s of t)this.onClickoutToClose(s,e)},!1)}bindToggleDocumentClass(t){for(let e of t)e.addEventListener("toggle",s=>{document.documentElement.classList.toggle(this.toggleDocumentClassName,s.target.open)})}}typeof window<"u"&&"customElements"in window&&window.customElements.define("details-utils",u);const r=document.querySelectorAll('[role="alert"]');r.length&&[...r].map(i=>{const t=i.querySelector('button[aria-label="Close"]');return t?window.matchMedia("(prefers-reduced-motion: reduce)").matches?t.addEventListener("click",()=>{i.remove()}):t.addEventListener("click",()=>{i.classList.add("transition-all","scale-100","opacity-100","duration-300"),i.classList.remove("opacity-100","scale-100"),i.classList.add("opacity-0","scale-0"),setTimeout(()=>{i.remove()},300)}):null});const f=[...document.querySelectorAll("[data-expander-button]")];f.map(i=>i.addEventListener("click",()=>{const t=i.getAttribute("data-expander-button"),e=document.querySelector(`[data-expander-list=${t}]`),s=e.getAttribute("aria-expanded")==="true";e.setAttribute("aria-expanded",`${!s}`),e.classList.toggle("hidden")}));const l=document.head.querySelector('meta[name="is_logged_in"]'),c=document.head.querySelector('meta[name="is_staff"]');if(document.location.hostname==="jobs.opensafely.org"){const i=document.createElement("script");i.defer=!0,i.setAttribute("data-domain","jobs.opensafely.org"),i.id="plausible",i.src="https://plausible.io/js/script.pageview-props.js",l&&i.setAttribute("event-is_logged_in",l.content),c&&i.setAttribute("event-is_staff",c.content),document.head.appendChild(i)}const d=(i=0)=>new Promise(t=>setTimeout(t,i));document.querySelectorAll(`input[type="submit"]:not(.btn--allow-double-click),
    button[type="submit"]:not(.btn--allow-double-click)`).forEach(i=>{i.addEventListener("click",async t=>{t.target instanceof HTMLElement&&(await d(1),t.target.setAttribute("disabled","true"),t.target.classList.add("!cursor-not-allowed","!bg-slate-300","!border-slate-300","!text-slate-800"),await d(3e4),t.target.removeAttribute("disabled"),t.target.classList.remove("!cursor-not-allowed","!bg-slate-300","!border-slate-300","!text-slate-800"))})});
