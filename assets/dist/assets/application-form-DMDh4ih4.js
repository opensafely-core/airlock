const s=document==null?void 0:document.getElementById("applicationForm"),r=s==null?void 0:s.getElementsByTagName("a");r!=null&&r.length&&[...r].map(e=>{e.hostname!==window.location.hostname&&(e.target="_blank",e.rel="noopener noreferrer")});const c=document==null?void 0:document.querySelectorAll("[data-character-count]");c==null||c.forEach(e=>{const n=e.parentElement.querySelector("textarea"),o=n.getAttribute("maxlength"),t=e.querySelector("[data-character-counter]"),l=e.querySelector("[data-typed-characters]");n.addEventListener("keyup",()=>{const a=n.value.length;return a>o?!1:(l.textContent=a,a>=1400&&a<1450?(t.classList.remove("text-bn-ribbon-800"),t.classList.add("text-bn-sun-800")):a>=1450?(t.classList.remove("text-bn-sun-800"),t.classList.add("text-bn-ribbon-800")):t.classList.remove(...t.classList))})});