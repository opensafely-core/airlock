const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/_datatable-BquxCQ6d.css"])))=>i.map(i=>d[i]);
import{_ as b}from"./preload-helper-DbXwivZQ.js";function h(e,t,i){e&&(t?(e.classList.remove("hidden"),e.addEventListener("click",()=>i.page(t))):e.classList.add("hidden"))}function y(){let e=document.querySelector('[data-table-pagination="next-page"]');e==null||e.replaceWith(e.cloneNode(!0)),e=document.querySelector('[data-table-pagination="next-page"]');let t=document.querySelector('[data-table-pagination="previous-page"]');return t==null||t.replaceWith(t.cloneNode(!0)),t=document.querySelector('[data-table-pagination="previous-page"]'),{nextPageBtn:e,previousPageBtn:t}}function l(e,t){const i=document.querySelector('[data-table-pagination="page-number"]'),r=document.querySelector('[data-table-pagination="total-pages"]'),{nextPageBtn:n,previousPageBtn:c}=y(),a=t.pages.length,o={currentPage:e,totalPages:a,nextPage:e<a?e+1:null,previousPage:e>1?e-1:null};i&&(i.innerHTML=JSON.stringify(o.currentPage)),r&&(r.innerHTML=JSON.stringify(o.totalPages)),h(n,o.nextPage,t),h(c,o.previousPage,t)}const N=async e=>{const t=document.querySelector("table#customTable"),i=e||25;if(t){const{DataTable:r}=await b(async()=>{const{DataTable:a}=await import("./module-CmxrMx2G.js");return{DataTable:a}},[]);await b(()=>Promise.resolve({}),__vite__mapDeps([0]));const n=new r(t,{paging:!0,perPage:i,searchable:!0,sortable:!0,tableRender:(a,o)=>{var d,u,p,g;const s=(d=o.childNodes)==null?void 0:d[0],T={nodeName:"TR",childNodes:(p=(u=s==null?void 0:s.childNodes)==null?void 0:u[0].childNodes)==null?void 0:p.map((v,m)=>({nodeName:"TH",childNodes:v.attributes["data-searchable"]!=="false"?[{nodeName:"INPUT",attributes:{class:"datatable-input","data-columns":`[${m}]`,placeholder:`Filter ${a.headings[m].text.trim().toLowerCase()}`,type:"search"}}]:[]}))};return(g=s==null?void 0:s.childNodes)==null||g.push(T),o},template:a=>`<div class='${a.classes.container}'></div>`});[...document.querySelectorAll("input.datatable-input")].map(a=>(a.addEventListener("input",()=>(a==null?void 0:a.value)===""?setTimeout(()=>l(1,n),0):null),null)),n.on("datatable.init",()=>l(1,n)),n.on("datatable.page",a=>l(a,n)),n.on("datatable.sort",()=>l(1,n)),n.on("datatable.search",()=>l(1,n))}};N();window.initCustomTable=N;