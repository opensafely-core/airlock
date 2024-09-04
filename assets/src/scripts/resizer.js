// Magic number for resizing
const MAGIC_PIXELS = 8;

/**
 * Debounce a function to slow down how frequently it runs.
 *
 * @param {number} ms - Time to wait before running the function
 * @param {function} fn - Function to run when being debounced
 * @returns {function}
 */
function debouncer(ms, fn) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(fn.bind(fn, args), ms);
  };
}

/**
 * Set the content height for either iframes, or workspace/request content,
 * so that it fills the available space.
 */
function setContentHeight() {
  const iframe = document.querySelector("iframe");
  if (iframe) {
    const iframeTop = iframe.getBoundingClientRect().top;
    iframe.style.height = `${window.innerHeight - iframeTop - MAGIC_PIXELS}px`;
  }

  const selectedContent = document.getElementById("selected-contents");
  if (selectedContent) {
    const contentTop = selectedContent.getBoundingClientRect().top;
    selectedContent.style.height = `${window.innerHeight - contentTop - MAGIC_PIXELS}px`;
    selectedContent.classList.add("overflow-auto");
  }
}

/**
 * Set the height of the tree container to fill the available space.
 */
function setTreeHeight() {
  const iframe = document.getElementById("tree-container");
  if (iframe) {
    const iframeTop = iframe.getBoundingClientRect().top;
    iframe.style.height = `${window.innerHeight - iframeTop - MAGIC_PIXELS}px`;
  }
}

/**
 * On page load, add and remove the relevant styling classes, so that the
 * content can fill the page.
 */
document.documentElement.classList.remove("min-h-screen");
document.documentElement.classList.add("h-screen");

document.body.classList.remove("min-h-screen");
document.body.classList.add("h-screen");

document.querySelector("main")?.classList.add("overflow-hidden");

/**
 * On browser resize, change the height of the content and file tree.
 *
 * Use the debouncer function to make sure this only runs when a user stops
 * dragging the window size.
 */
const ro = new ResizeObserver(
  debouncer(100, () => {
    setContentHeight();
    setTreeHeight();
  }),
);

ro.observe(document.documentElement);

/**
 * When the user selects a file from the tree, HTMX replaces the content.
 * Listen for this change, and reset the content height after the file content
 * has been loaded.
 */
document.body.addEventListener("htmx:afterSettle", () => setContentHeight());
